import logging
import json
import os
import requests
import azure.functions as func
from azure.data.tables import TableServiceClient, UpdateMode
from azure.storage.queue import QueueClient
from datetime import datetime

# --- KONSTANTA (MENGHINDARI REDUNDANSI PROMPT) ---
AI_ENDPOINT = os.environ.get("AI_SERVICE_ENDPOINT")
TABLE_CONN = os.environ.get("AzuriteTableConnection")
QUEUE_CONN = os.environ.get("AzuriteQueueConnection")
CATEGORIZED_QUEUE_NAME = "transaction-categorized"

# Prompt LLM yang telah distrukturkan (Dapat diletakkan di file config terpisah di proyek nyata)
CATEGORIZATION_PROMPT = (
    "Anda adalah mesin ekstraksi dan kategorisasi transaksi finansial. Tugas Anda adalah mengidentifikasi "
    "jumlah uang dan kategori dari deskripsi. Kategorikan ke 'Makanan & Minuman', 'Transportasi', "
    " 'Kebutuhan Harian' atau 'Lainnya'. Berikan respons HANYA dalam format JSON dengan kunci 'category' (string), "
    "'amount' (float), dan 'confidence' (float)."
)

# Simulasi category mapping lookup (Di proyek nyata, ini diambil dari Category Table)
CATEGORY_MAP = {
    "Makanan & Minuman": 1,
    "Transportasi": 2,
    "Kebutuhan Harian": 3,
    "Uncategorized": 0 # Default jika AI gagal
}

def main(msg: func.QueueMessage):
    # 1. Menerima dan Mem-parse Event
    try:
        # Event berisi seluruh entitas transaksi yang baru dibuat dari Transaction Service
        transaction_data = json.loads(msg.get_body().decode('utf-8'))
        transaction_pk = transaction_data["PartitionKey"]
        transaction_rk = transaction_data["RowKey"]
        transaction_desc = transaction_data.get("description", "")
        
        logging.info(f"Received transaction for categorization: {transaction_rk}")
        
    except Exception as e:
        logging.error(f"Error parsing transaction event: {e}")
        return

    # 2. Orkestrasi AI: Memanggil LanguageFunction (Gemini)
    try:
        ai_instructions = {
            "model_name": "gemini-2.5-flash", 
            "system_prompt": CATEGORIZATION_PROMPT # Gunakan konstanta
        }
        
        payload = {"text": transaction_desc, "instructions": ai_instructions}
        
        response = requests.post(AI_ENDPOINT, json=payload)
        response.raise_for_status() 

        ai_result = response.json()
        
        # Ekstraksi Hasil AI
        predicted_name = ai_result.get("category", "Uncategorized")
        ai_confidence = ai_result.get("ai_confidence", 0.0)
        
        # Ekstraksi dan konversi amount
        extracted_amount = float(ai_result.get("amount", transaction_data.get("amount", 0.0)))
        
        # Mapping nama kategori ke ID (menggunakan lookup map)
        predicted_id = CATEGORY_MAP.get(predicted_name, 0) 
        
        logging.info(f"AI categorized {transaction_rk}: {predicted_name}, Amount: {extracted_amount}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling AI Service: {e}")
        # Tetapkan nilai default aman jika AI gagal
        predicted_id = 0
        ai_confidence = 0.0
        extracted_amount = transaction_data.get("amount", 0.0) # Pertahankan amount yang sudah ada/default
        predicted_name = "AI_FAILED"

    # 3. Update Transaksi di Azurite
    try:
        table_service = TableServiceClient.from_connection_string(TABLE_CONN)
        transaction_table = table_service.get_table_client("transaction")

        updated_entity = {
            "PartitionKey": transaction_pk,
            "RowKey": transaction_rk,
            "category_id": predicted_id,
            "amount": extracted_amount, # <--- NILAI BARU DARI AI
            "ai_confidence": str(ai_confidence),
            "ai_category_name": predicted_name
        }
        
        transaction_table.update_entity(entity=updated_entity, mode=UpdateMode.MERGE)
        logging.info(f"Transaction {transaction_rk} successfully updated in DB.")

    except Exception as e:
        logging.error(f"Error updating transaction table: {e}")
        return

    # 4. Publish Event Selesai Kategorisasi
    try:
        queue_client = QueueClient.from_connection_string(conn_str=QUEUE_CONN, queue_name=CATEGORIZED_QUEUE_NAME)
        
        # Event baru berisi data transaksi yang sudah dikategorikan
        updated_data = transaction_data.copy()
        updated_data["category_id"] = predicted_id
        updated_data["amount"] = extracted_amount # Pastikan amount yang baru juga dikirim
        
        queue_client.create_queue()
        queue_client.send_message(json.dumps(updated_data))
        
        logging.info(f"Event published to {CATEGORIZED_QUEUE_NAME}")
        
    except Exception as e:
        logging.error(f"Error publishing categorized event: {e}")
        
    # Fungsi selesai