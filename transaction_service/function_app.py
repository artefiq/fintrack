import azure.functions as func
import logging
import json
import os
import uuid
from datetime import datetime
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient

app = func.FunctionApp()

# --- KONFIGURASI ---
# Mengambil connection string dari environment variable
# Di Docker, ini akan menunjuk ke 'azurite'. Di lokal, ke '127.0.0.1'.
CONN_STR = os.environ.get("AZURITE_CONN_STR")
OUTPUT_QUEUE_NAME = "transaction-created"
TABLE_NAME = "transaction"

@app.route(route="transaction/create", methods=["POST"])
def CreateTransaction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing Create Transaction request.')

    try:
        req_body = req.get_json()
        
        # 1. Validasi Input Minimal
        if not req_body or "user_id" not in req_body or "description" not in req_body:
            return func.HttpResponse(
                json.dumps({"error": "Missing user_id or description"}),
                status_code=400,
                mimetype="application/json"
            )

        # 2. Siapkan Data Entity
        # Kita set amount ke 0.0 jika user tidak input, agar AI nanti yang mengisinya.
        row_key = str(uuid.uuid4())
        
        entity = {
            "PartitionKey": "transaction",
            "RowKey": row_key,
            "user_id": req_body["user_id"],
            "description": req_body["description"],
            "category_id": 0,          # Default: Uncategorized
            "amount": float(req_body.get("amount", 0.0)), 
            "transaction_date": datetime.utcnow().isoformat(),
            "source": req_body.get("source", "manual_input"),
            "ai_confidence": "0.0",    # Belum diproses AI
            "ai_category_name": "Pending"
        }

        # 3. Simpan ke Table Storage (Raw Data)
        table_service = TableServiceClient.from_connection_string(CONN_STR)
        table_client = table_service.get_table_client(TABLE_NAME)
        
        # Buat tabel jika belum ada (safety check)
        try:
            table_client.create_entity(entity=entity)
            logging.info(f"Transaction {row_key} saved to Table Storage.")
        except Exception as e:
            # Jika tabel belum ada, buat dulu (jarang terjadi jika init_tables.py sudah jalan)
            table_service.create_table_if_not_exists(TABLE_NAME)
            table_client.create_entity(entity=entity)

        # 4. Kirim ke Queue untuk diproses Category Service (AI)
        queue_client = QueueClient.from_connection_string(CONN_STR, OUTPUT_QUEUE_NAME)
        
        # Encode message ke JSON string
        message_content = json.dumps(entity)
        
        try:
            queue_client.send_message(message_content)
        except Exception:
             # Auto-create queue jika belum ada
            queue_client.create_queue()
            queue_client.send_message(message_content)

        logging.info(f"Event published to queue: {OUTPUT_QUEUE_NAME}")

        # 5. Return Success
        return func.HttpResponse(
            json.dumps({
                "message": "Transaction created", 
                "id": row_key,
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