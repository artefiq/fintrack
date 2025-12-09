import logging
import azure.functions as func
import json
import uuid
import os
from datetime import datetime
import reverse_geocoder as rg # Asumsi kamu pake library ini
from azure.cosmos import CosmosClient # <--- Library baru
from azure.storage.queue import QueueClient

# Konfigurasi Environment Variable
COSMOS_CONN_STR = os.environ.get("COSMOS_CONN_STR")
QUEUE_CONN_STR = os.environ.get("STORAGE_CONN_STR")
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_CONTAINER_NAME")
OUTPUT_QUEUE_NAME = os.environ.get("STORAGE_QUEUE_NAME")

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="transaction/create", methods=["POST"])
def CreateTransaction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing Create Transaction request (Cosmos DB Version).')

    try:
        req_body = req.get_json()
        
        # 1. Validasi Input Minimal
        if not req_body or "user_id" not in req_body or "description" not in req_body:
            return func.HttpResponse(
                json.dumps({"error": "Missing user_id or description"}),
                status_code=400,
                mimetype="application/json"
            )

        # 2. PROSES REVERSE GEOCODER
        # Kita siapkan default object location dulu
        lat = req_body.get("latitude")
        lon = req_body.get("longitude")
        
        location_obj = {
            "city": "Unknown",
            "country": "Unknown"
        }

        if lat is not None and lon is not None:
            try:
                # Asumsi rg.search mengembalikan list dictionary
                result = rg.search((lat, lon))[0]
                location_obj = {
                    "city": result.get("name", "Unknown"),
                    "country": result.get("cc", "Unknown")
                }
                logging.info(f"Reverse geocode result: {location_obj}")
            except Exception as e:
                logging.error(f"Reverse geocoder failed: {str(e)}")

        # 3. BENTUK DOKUMEN NoSQL (JSON)
        # Ini struktur baru sesuai request kamu (Embedded & Grouping)
        transaction_id = str(uuid.uuid4())
        
        document = {
            # --- IDENTITAS DOKUMEN ---
            "id": transaction_id,           # ID Unik Transaksi
            "user_id": req_body["user_id"], # PARTITION KEY (Wajib ada!)
            "type": "transaction",          # Penanda jenis data
            
            # --- DATA TRANSAKSI ---
            "amount": float(req_body.get("amount", 0.0)),
            "description": req_body["description"],
            "transaction_date": datetime.utcnow().isoformat(),
            
            # --- EMBEDDED OBJECTS ---
            # Lokasi dijadikan satu object
            "location": location_obj,

            # Kategori Default (Pending)
            # Kita isi placeholder dulu karena AI belum jalan
            "category": {
                "id": "0",
                "name": "Pending Categorization",
                "category_type": "Uncategorized"
            },
            
            # --- METADATA ---
            "source": req_body.get("source", "manual_input"),
            "input_type": "text",
            "ai_confidence": "0.0",       # Belum ada confidence karena belum diproses AI
            "is_processed": False         # Flag penanda kalau mau diproses worker lain
        }

        # 4. SIMPAN KE COSMOS DB
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)
        
        # create_item otomatis throw error kalau gagal, jadi cukup try-except luar
        container.create_item(body=document)

        # 5. KIRIM KE QUEUE (Untuk diproses AI nanti)
        # Worker AI nanti baca queue -> update category -> patch cosmos db
        queue_client = QueueClient.from_connection_string(QUEUE_CONN_STR, OUTPUT_QUEUE_NAME)
        message_content = json.dumps(document)
        
        try:
            queue_client.send_message(message_content)
        except Exception:
            # Fallback kalau queue belum ada (development only)
            queue_client.create_queue()
            queue_client.send_message(message_content)

        # 6. RESPON SUKSES
        return func.HttpResponse(
            json.dumps({
                "message": "Transaction created successfully",
                "id": transaction_id,
                "data": document, # Balikin data lengkap biar FE bisa langsung update UI
                "status": "queued_for_categorization"
            }),
            status_code=201,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error creating transaction: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

@app.route(route="transaction/get", methods=["GET"])
def GetTransaction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Getting transaction detail.')
    
    # Ambil parameter dari Query String (?id=...&user_id=...)
    # user_id WAJIB karena dia adalah Partition Key Cosmos DB Anda
    doc_id = req.params.get('id')
    user_id = req.params.get('user_id')

    if not doc_id or not user_id:
        return func.HttpResponse(
            json.dumps({"error": "Please provide id and user_id"}),
            status_code=400
        )

    try:
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)

        # Baca Item dari Cosmos DB
        # read_item butuh ID dan Partition Key
        item = container.read_item(item=doc_id, partition_key=user_id)
        
        return func.HttpResponse(
            json.dumps(item),
            status_code=200,
            mimetype="application/json"
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": "Transaction not found or error"}),
            status_code=404
        )