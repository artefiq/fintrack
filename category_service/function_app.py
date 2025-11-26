import logging
import json
import os
import uuid
import requests
import azure.functions as func
from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

app = func.FunctionApp()

# --- KONFIGURASI ---
AI_ENDPOINT = os.environ.get("AI_SERVICE_ENDPOINT")
COSMOS_CONN_STR = os.environ.get("COSMOS_CONN_STR")
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_CONTAINER_NAME") # Satu container untuk semua
INPUT_QUEUE_NAME = os.environ.get("STORAGE_QUEUE_NAME")

# --- PROMPT AI ---
# Kita minta AI mengembalikan nama & tipe agar sesuai struktur DB Category Anda
CATEGORIZATION_PROMPT = (
    "Identifikasi jumlah uang, nama kategori dan tipe kategori. "
    "Nama kategori HARUS salah satu dari: 'Makanan & Minuman', 'Transportasi', 'Kebutuhan Harian', 'Gaji', 'Lainnya'. Tipe kategori: 'Expense' atau 'Income'. "
    "Output berupa JSON: {'category_name': string, 'category_type': string, 'amount': float, 'ai_confidence': float}."
)

# --- HELPER: Get or Create Category (Cosmos NoSQL Style) ---
def get_or_create_category_snapshot(container, category_name, category_type):
    """
    Mencari kategori di Cosmos DB berdasarkan name + type.
    Jika tidak ada, buat dokumen 'type': 'category' baru.
    Mengembalikan objek snapshot kategori untuk di-embed ke transaksi.
    """
    # 1. Query ke Cosmos DB (Wajib filter by user_id agar Partition Key kena)
    # Kita cari dokumen yang type='category' DAN namanya sama
    ADMIN_PK = "ADMIN"
    query = "SELECT * FROM c WHERE c.type = 'category' AND c.user_id = @user_id AND c.name = @name AND c.category_type = @category_type"
    parameters = [
        {"name": "@user_id", "value": ADMIN_PK},
        {"name": "@name", "value": category_name},
        {"name": "@category_type", "value": category_type}
    ]

    items = list(container.query_items(
        query=query,
        parameters=parameters,
        enable_cross_partition_query=False # False karena kita supply user_id
    ))

    if items:
        # Kategori Ditemukan
        existing_cat = items[0]
        logging.info(f"Category Found: {existing_cat['name']} (ID: {existing_cat['id']})")
        
        # Return snapshot object sesuai struktur di gambar transaksi Anda
        return {
            "id": existing_cat['id'],
            "name": existing_cat['name'],
            "category_type": existing_cat['category_type']
        }
    else:
        # Kategori Baru -> Create Document
        new_cat_id = str(uuid.uuid4())
        logging.info(f"Creating New Category: {category_name}")

        new_category_doc = {
            "id": new_cat_id,
            "user_id": user_id,       # Partition Key
            "type": "category",       # Discriminator
            "name": ADMIN_PK,
            "category_type": category_type,
        }

        container.create_item(body=new_category_doc)

        return {
            "id": new_cat_id,
            "name": category_name,
            "category_type": category_type
        }

@app.queue_trigger(arg_name="msg", queue_name=INPUT_QUEUE_NAME, connection="STORAGE_CONN_STR")
def CategoryProcessor(msg: func.QueueMessage):
    logging.info(">>> PROCESSING TRANSACTION FROM QUEUE (COSMOS DB)...")

    # 1. Parse Event dari Queue
    try:
        message_body = msg.get_body().decode('utf-8')
        transaction_doc = json.loads(message_body) # Ini dokumen utuh dari TransactionService
        
        transaction_id = transaction_doc.get("id")
        user_id = transaction_doc.get("user_id") # PENTING UNTUK PARTITION KEY
        description = transaction_doc.get("description", "")
        current_amount = float(transaction_doc.get("amount", 0.0))
        
        if not transaction_id or not user_id:
            logging.error("Invalid Message: Missing id or user_id")
            return

    except Exception as e:
        logging.error(f"Error parsing queue: {e}")
        return

    # 2. Panggil AI Service
    predicted_name = "Lainnya"
    predicted_type = "Expense"
    ai_confidence = 0.0
    detected_amount = 0.0

    try:
        # Payload ke AI Service
        payload = {
            "text": description,
            "instructions": {
                "model_name": "gemini-2.5-flash", 
                "system_prompt": CATEGORIZATION_PROMPT
            }
        }
        
        response = requests.post(AI_ENDPOINT, json=payload, timeout=10)
        
        if response.status_code == 200:
            ai_result = response.json()
            predicted_name = ai_result.get("category_name", "Lainnya")
            predicted_type = ai_result.get("category_type", "Expense")
            ai_confidence = ai_result.get("ai_confidence", 0.0)
            if "amount" in ai_result and ai_result["amount"] > 0:
                detected_amount = float(ai_result["amount"])
        else:
            logging.warning(f"AI Service non-200: {response.text}")

    except Exception as e:
        logging.error(f"AI Service Failed: {e}. Using Default.")

    # 3. Update Cosmos DB
    try:
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)

        # A. Dapatkan Object Kategori (Snapshot)
        # Logic: Cari di DB -> Kalau ga ada buat baru -> Kembalikan {id, name, type}
        category_snapshot = get_or_create_category_snapshot(
            container, 
            predicted_name, 
            predicted_type
        )

        # B. Siapkan Operasi Patch
        patch_ops = [
            { "op": "replace", "path": "/category", "value": category_snapshot },
            { "op": "replace", "path": "/ai_confidence", "value": str(ai_confidence) },
            { "op": "replace", "path": "/is_processed", "value": True }
        ]

        if detected_amount > 0:
            logging.info(f"AI detected new amount: {detected_amount} (Old: {current_amount})")
            patch_ops.append({ "op": "replace", "path": "/amount", "value": detected_amount })

        # Eksekusi Patch
        container.patch_item(
            item=transaction_id,
            partition_key=user_id,
            patch_operations=patch_ops
        )
        
        logging.info("SUCCESS: Transaction updated in Cosmos DB.")

    except Exception as e:
        logging.error(f"Database Update Failed: {e}")
        raise e