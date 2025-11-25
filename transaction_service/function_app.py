import azure.functions as func
import logging
import json
import os
import uuid
from datetime import datetime
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient
import reverse_geocoder as rg

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

        # Ambil lat/lon jika ada
        lat = req_body.get("latitude")
        lon = req_body.get("longitude")

        location_info = {
            "city": "Unknown",
            "region": "Unknown",
            "country": "Unknown"
        }

        # 2. PROSES REVERSE GEOCODER (kalau ada koordinat)
        if lat is not None and lon is not None:
            try:
                result = rg.search((lat, lon))[0]
                location_info = {
                    "city": result.get("name", "Unknown"),
                    "country": result.get("cc", "Unknown")
                }
                logging.info(f"Reverse geocode: {location_info}")
            except Exception as e:
                logging.error(f"Reverse geocoder failed: {str(e)}")

        row_key = str(uuid.uuid4())
        
        entity = {
            "PartitionKey": "transaction",
            "RowKey": row_key,
            "user_id": req_body["user_id"],
            "description": req_body["description"],

            # NEW — lokasi hasil reverse geocoder
            "city": location_info["city"],
            "country": location_info["country"],
            "input_type": "text",
            "category_id": 0,
            "amount": float(req_body.get("amount", 0.0)), 
            "transaction_date": datetime.utcnow().isoformat(),
            "source": req_body.get("source", "manual_input"),
            "ai_confidence": "0.0",
            "ai_category_name": "Pending"
        }

        # 3. Simpan ke Table Storage
        table_service = TableServiceClient.from_connection_string(CONN_STR)
        table_client = table_service.get_table_client(TABLE_NAME)
        
        try:
            table_client.create_entity(entity=entity)
        except Exception:
            table_service.create_table_if_not_exists(TABLE_NAME)
            table_client.create_entity(entity=entity)

        # 4. Kirim ke queue
        queue_client = QueueClient.from_connection_string(CONN_STR, OUTPUT_QUEUE_NAME)
        
        message_content = json.dumps(entity)
        
        try:
            queue_client.send_message(message_content)
        except Exception:
            queue_client.create_queue()
            queue_client.send_message(message_content)

        # 5. Respon
        return func.HttpResponse(
            json.dumps({
                "message": "Transaction created",
                "id": row_key,
                "location": location_info,
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