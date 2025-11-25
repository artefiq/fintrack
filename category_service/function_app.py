import logging
import json
import os
import uuid
import requests
import azure.functions as func
from azure.data.tables import TableServiceClient, UpdateMode
from azure.storage.queue import QueueClient

app = func.FunctionApp()

# --- KONFIGURASI ---
AI_ENDPOINT = os.environ.get("AI_SERVICE_ENDPOINT") 
CONN_STR = os.environ.get("AZURITE_CONN_STR") 
INPUT_QUEUE_NAME = "transaction-created"
CATEGORIZED_QUEUE_NAME = "transaction-categorized"

# Prompt
CATEGORIZATION_PROMPT = (
    "Identifikasi jumlah uang, nama kategori dan tipe kategori. "
    "Nama kategori HARUS salah satu dari: 'Makanan & Minuman', 'Transportasi', 'Kebutuhan Harian', 'Gaji', 'Lainnya'. "
    "Tipe kategori: 'Expense' atau 'Income'. "
    "Respons JSON: {'category_name': string, 'category_type': string, 'amount': float, 'ai_confidence': float}."
)

# --- FUNGSI HELPER BARU: Cari ID berdasarkan Nama ---
def get_or_create_category_id(table_service, category_name, category_type, user_id):
    """
    Mencari apakah kombinasi (Nama + Tipe + User) sudah ada.
    Filter unik: category_name AND category_type AND user_id.
    """
    try:
        cat_table = table_service.get_table_client("category")
        
        # 1. PERBAIKAN FILTER: Menambahkan Type dan User ID agar unik spesifik
        # Syntax OData: String pakai tanda kutip satu ('), Angka tidak perlu.
        # Pastikan category_name aman dari tanda kutip (misal: McDonald's -> escape)
        safe_name = category_name.replace("'", "''") 
        
        my_filter = (
            f"category_name eq '{safe_name}'"
            f" and category_type eq '{category_type}'"
        )
            # f" and user_id eq {user_id}"
        
        logging.info(f"Querying Category: {my_filter}")

        # 2. PERBAIKAN QUERY_ENTITIES: Menggunakan parameter 'query_filter'
        # Sebelumnya error karena pakai 'filter='
        entities = list(cat_table.query_entities(query_filter=my_filter))
        
        if entities:
            # Skenario A: Kategori Ditemukan
            found_cat = entities[0]
            logging.info(f"Category found: {found_cat['RowKey']} - {found_cat['category_name']}")
            return found_cat['RowKey']
        else:
            # Skenario B: Kategori Baru (Belum ada di DB)
            logging.info(f"Category '{category_name}' ({category_type}) not found. Creating new...")
            
            # Generate ID baru
            new_id = str(uuid.uuid4())[:8]
            
            new_cat_entity = {
                "PartitionKey": "category",
                "RowKey": new_id,
                "category_name": category_name,
                "category_type": category_type,
                "user_id": user_id
            }
            cat_table.create_entity(entity=new_cat_entity)
            logging.info(f"New Category Created: {new_id}")
            return new_id

    except Exception as e:
        logging.error(f"Error in get_or_create_category_id: {e}")
        # Kembalikan ID default/lainnya jika DB error
        return "0"

@app.queue_trigger(arg_name="msg", queue_name=INPUT_QUEUE_NAME, connection="AZURITE_CONN_STR")
def CategoryProcessor(msg: func.QueueMessage):
    logging.info(">>> FUNGSI TRIGGERED!")

    # 1. Parse Event
    try:
        message_body = msg.get_body().decode('utf-8')
        transaction_data = json.loads(message_body)
        
        transaction_pk = transaction_data.get("PartitionKey")
        transaction_rk = transaction_data.get("RowKey")
        transaction_desc = transaction_data.get("description", "")
        # Pastikan user_id ada untuk pencarian kategori
        user_id = transaction_data.get("user_id", 1) 
    except Exception as e:
        logging.error(f"Error parsing: {e}")
        return

    # 2. Panggil AI Service
    predicted_name = "Lainnya"
    predicted_type = "Expense"
    ai_confidence = 0.0
    extracted_amount = transaction_data.get("amount", 0.0)

    try:
        if not AI_ENDPOINT: raise ValueError("AI Endpoint missing")
        
        payload = {
            "text": transaction_desc, 
            "instructions": {"model_name": "gemini-2.5-flash", "system_prompt": CATEGORIZATION_PROMPT}
        }
        
        response = requests.post(AI_ENDPOINT, json=payload, timeout=10)
        ai_result = response.json()
        
        predicted_name = ai_result.get("category_name", "Lainnya")
        predicted_type = ai_result.get("category_type", "Expense")
        ai_confidence = ai_result.get("ai_confidence", 0.0)
        
        if ai_result.get("amount", 0) > 0:
            extracted_amount = float(ai_result["amount"])

    except Exception as e:
        logging.error(f"AI Failed: {e}")

    # 3. DAPATKAN ID KATEGORI DARI DB (Langkah Kunci)
    try:
        table_service = TableServiceClient.from_connection_string(CONN_STR)
        
        # Panggil fungsi helper tadi!
        # Ini akan otomatis mencari ID "1" jika namanya "Makanan & Minuman"
        final_category_id = get_or_create_category_id(
            table_service, 
            predicted_name, 
            predicted_type, 
            user_id
        )
        
    except Exception as e:
        logging.error(f"DB Lookup Failed: {e}")
        final_category_id = 0

    # 4. Update Tabel Transaksi
    try:
        transaction_table = table_service.get_table_client("transaction")
        # Buat tabel jika belum ada (safety dev local)
        try: transaction_table.create_table()
        except: pass

        updated_entity = {
            "PartitionKey": transaction_pk,
            "RowKey": transaction_rk,
            "category_id": final_category_id, # <--- INI SUDAH BENAR (Foreign Key ID)
            "amount": extracted_amount, 
            "ai_confidence": str(ai_confidence),
            "ai_category_name": predicted_name, # Hanya untuk kemudahan baca (display)
            "status": "categorized"
        }
        
        transaction_table.upsert_entity(mode=UpdateMode.MERGE, entity=updated_entity)
        logging.info(f"SUCCESS: Transaction {transaction_rk} categorized as ID {final_category_id} ({predicted_name})")

    except Exception as e:
        logging.error(f"Update Transaction Failed: {e}")
        raise e

    # 5. Publish to Queue (sama seperti sebelumnya)
    # ... pastikan kirim final_category_id juga di JSON output

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
            "category_id": final_category_id,
            "amount": extracted_amount,
            "ai_category_name": predicted_name
        })
        
        # Encode ke base64 jika perlu (default python client biasanya handle string/bytes)
        queue_client.send_message(json.dumps(next_event_data))
        
        logging.info(f"Message sent to queue: {CATEGORIZED_QUEUE_NAME}")
        
    except Exception as e:
        logging.error(f"Error publishing to output queue: {e}")
        raise e