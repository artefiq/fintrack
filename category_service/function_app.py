import logging
import json
import os
import requests
import azure.functions as func
from azure.data.tables import TableServiceClient, UpdateMode
from azure.storage.queue import QueueClient

app = func.FunctionApp()

# --- KONFIGURASI & KONSTANTA ---
# Mengambil dari environment variable (diset di docker-compose atau local.settings.json)
AI_ENDPOINT = os.environ.get("AI_SERVICE_ENDPOINT") 
CONN_STR = os.environ.get("AZURITE_CONN_STR") # Satu connection string untuk Table & Queue di Azurite

CATEGORIZED_QUEUE_NAME = "transaction-categorized"
INPUT_QUEUE_NAME = "transaction-created"

# Prompt LLM
CATEGORIZATION_PROMPT = (
    "Anda adalah mesin ekstraksi dan kategorisasi transaksi finansial. Tugas Anda adalah mengidentifikasi "
    "jumlah uang dan kategori dari deskripsi. Kategorikan ke 'Makanan & Minuman', 'Transportasi', "
    " 'Kebutuhan Harian' atau 'Lainnya'. Berikan respons HANYA dalam format JSON dengan kunci 'category' (string), "
    "'amount' (float), dan 'confidence' (float)."
)

# Category Mapping (Simulasi DB Kategori)
CATEGORY_MAP = {
    "Makanan & Minuman": 1,
    "Transportasi": 2,
    "Kebutuhan Harian": 3,
    "Lainnya": 4,
    "Uncategorized": 0
}

@app.queue_trigger(arg_name="msg", queue_name=INPUT_QUEUE_NAME, connection="AZURITE_CONN_STR")
def CategoryProcessor(msg: func.QueueMessage):
    # 1. Menerima dan Mem-parse Event
    try:
        message_body = msg.get_body().decode('utf-8')
        transaction_data = json.loads(message_body)
        
        transaction_pk = transaction_data.get("PartitionKey")
        transaction_rk = transaction_data.get("RowKey")
        transaction_desc = transaction_data.get("description", "")
        
        logging.info(f"Processing transaction: {transaction_rk} - {transaction_desc}")
        
    except Exception as e:
        logging.error(f"Error parsing queue message: {e}")
        return

    # 2. Orkestrasi AI: Memanggil AI Service
    predicted_name = "Uncategorized"
    predicted_id = 0
    ai_confidence = 0.0
    extracted_amount = transaction_data.get("amount", 0.0)

    try:
        if not AI_ENDPOINT:
            raise ValueError("AI_SERVICE_ENDPOINT not set")

        ai_instructions = {
            "model_name": "gemini-2.0-flash", 
            "system_prompt": CATEGORIZATION_PROMPT
        }
        
        payload = {"text": transaction_desc, "instructions": ai_instructions}
        
        # Panggil AI Service (Internal Docker Call)
        response = requests.post(AI_ENDPOINT, json=payload, timeout=10)
        response.raise_for_status() 

        ai_result = response.json()
        
        # Ekstraksi Hasil
        predicted_name = ai_result.get("category", "Uncategorized")
        ai_confidence = ai_result.get("ai_confidence", 0.0)
        # Update amount jika AI menemukan angka yang lebih akurat dari deskripsi
        if "amount" in ai_result and ai_result["amount"] > 0:
            extracted_amount = float(ai_result["amount"])
        
        predicted_id = CATEGORY_MAP.get(predicted_name, 0) # Default 0 jika tidak ada di map
        
        logging.info(f"AI Result for {transaction_rk}: {predicted_name} (ID: {predicted_id})")

    except Exception as e:
        logging.error(f"AI Service failed or unreachable: {e}. Using defaults.")
        # Kita tetap lanjut agar transaksi tidak macet, tapi ditandai uncategorized

    # 3. Update Transaksi di Database (Azurite Table)
    try:
        table_service = TableServiceClient.from_connection_string(CONN_STR)
        transaction_table = table_service.get_table_client("transaction")

        updated_entity = {
            "PartitionKey": transaction_pk,
            "RowKey": transaction_rk,
            "category_id": predicted_id,
            "amount": extracted_amount, 
            "ai_confidence": str(ai_confidence),
            "ai_category_name": predicted_name,
            "status": "categorized" # Menandakan proses ini selesai
        }
        
        # Merge update (hanya update field yang berubah)
        transaction_table.update_entity(entity=updated_entity, mode=UpdateMode.MERGE)
        logging.info(f"DB Updated for {transaction_rk}")

    except Exception as e:
        logging.error(f"Error updating Table Storage: {e}")
        # Jika DB gagal update, kita mungkin ingin me-raise error agar queue di-retry
        raise e

    # 4. Publish Event ke Queue Selanjutnya (transaction-categorized)
    # Service lain (misal: Budget Service / Notification Service) bisa dengar queue ini
    try:
        queue_client = QueueClient.from_connection_string(conn_str=CONN_STR, queue_name=CATEGORIZED_QUEUE_NAME)
        
        # Pastikan queue ada
        try:
            queue_client.create_queue()
        except:
            pass # Queue already exists
        
        # Siapkan data untuk next stage
        next_event_data = transaction_data.copy()
        next_event_data.update({
            "category_id": predicted_id,
            "amount": extracted_amount,
            "ai_category_name": predicted_name
        })
        
        # Encode ke base64 jika perlu (default python client biasanya handle string/bytes)
        queue_client.send_message(json.dumps(next_event_data))
        
        logging.info(f"Message sent to queue: {CATEGORIZED_QUEUE_NAME}")
        
    except Exception as e:
        logging.error(f"Error publishing to output queue: {e}")
        raise e