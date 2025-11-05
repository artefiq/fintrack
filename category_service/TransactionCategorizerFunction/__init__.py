import logging
import json
import os
import requests
import azure.functions as func
from azure.data.tables import TableServiceClient, UpdateMode
from azure.storage.queue import QueueClient
from datetime import datetime

# URL endpoint AI Service lokal Anda (diambil dari local.settings.json)
AI_ENDPOINT = os.environ.get("AI_SERVICE_ENDPOINT")
TABLE_CONN = os.environ.get("AzuriteTableConnection")
QUEUE_CONN = os.environ.get("AzuriteQueueConnection")

# Nama queue untuk event setelah kategorisasi selesai
CATEGORIZED_QUEUE_NAME = "transaction-categorized"

def main(msg: func.QueueMessage):
    # 1. Menerima dan Mem-parse Event dari Transaction Service
    try:
        transaction_data = json.loads(msg.get_body().decode('utf-8'))
        
        # Data penting dari transaksi
        transaction_pk = transaction_data["PartitionKey"]
        transaction_rk = transaction_data["RowKey"]
        transaction_desc = transaction_data.get("description", "")
        
        logging.info(f"Received transaction for categorization: {transaction_rk}")
        
    except Exception as e:
        logging.error(f"Error parsing transaction event: {e}")
        return

    # 2. Orkestrasi AI: Memanggil LanguageFunction (Gemini)
    try:
        # Menyiapkan instruksi untuk LLM (Instruksi dari Category Service)
        ai_instructions = {
            "model_name": "gemini-2.5-flash", 
            "system_prompt": "Anda adalah mesin kategorisasi keuangan Fintrack. Kategorikan transaksi ke dalam kategori 'Makanan & Minuman', 'Transportasi', 'Kebutuhan Harian', 'Pemasukan', atau 'Lain-Lain'. Berikan kategori dan confidence score."
        }
        
        payload = {
            "text": transaction_desc,
            "instructions": ai_instructions
        }
        
        # Panggilan HTTP POST ke AI Service
        response = requests.post(AI_ENDPOINT, json=payload)
        response.raise_for_status() # Lempar error jika status code 4xx atau 5xx

        ai_result = response.json()
        
        predicted_category_name = ai_result.get("category", "Uncategorized")
        ai_confidence_score = ai_result.get("ai_confidence", 0.0)
        
        # TODO: Logika mapping kategori dari nama ke category_id (sesuai category_table)
        # Untuk demo, kita asumsikan 1 = Makanan, 2 = Transportasi, dll.
        # Jika category_name = Makanan & Minuman, maka category_id = 1
        predicted_category_id = 1 # Placeholder
        
        logging.info(f"AI categorized {transaction_rk} as: {predicted_category_name}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling AI Service at {AI_ENDPOINT}: {e}")
        # Jika AI service gagal, tetapkan nilai default dan proses tetap berlanjut
        predicted_category_id = 0
        ai_confidence_score = 0.0

    # 3. Update Transaksi di Azurite (Penyimpanan)
    try:
        table_service = TableServiceClient.from_connection_string(TABLE_CONN)
        transaction_table = table_service.get_table_client("transaction")

        # Entitas yang akan diupdate
        updated_entity = {
            "PartitionKey": transaction_pk,
            "RowKey": transaction_rk,
            "category_id": predicted_category_id,
            "ai_confidence": str(ai_confidence_score),
            "ai_category_name": predicted_category_name # Simpan nama untuk referensi
        }
        
        # Menggunakan MERGE (UpdateMode.REPLACE) untuk update entitas
        transaction_table.update_entity(entity=updated_entity, mode=UpdateMode.MERGE)
        logging.info(f"Transaction {transaction_rk} updated with category ID {predicted_category_id}")

    except Exception as e:
        logging.error(f"Error updating transaction table: {e}")
        return

    # 4. Publish Event Selesai Kategorisasi
    try:
        queue_client = QueueClient.from_connection_string(
            conn_str=QUEUE_CONN,
            queue_name=CATEGORIZED_QUEUE_NAME
        )
        # Event baru berisi data transaksi yang sudah dikategorikan
        updated_data = transaction_data.copy()
        updated_data["category_id"] = predicted_category_id
        
        queue_client.create_queue()
        queue_client.send_message(json.dumps(updated_data))
        
        logging.info(f"Event published to {CATEGORIZED_QUEUE_NAME} for {transaction_rk}")
        
    except Exception as e:
        logging.error(f"Error publishing categorized event: {e}")