import azure.functions as func
import logging
import json
import uuid
import os
from datetime import datetime
import reverse_geocoder as rg
from azure.cosmos import CosmosClient
from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient

# --- KONFIGURASI ENVIRONMENT ---
COSMOS_CONN_STR = os.environ.get("COSMOS_CONN_STR")
STORAGE_CONN_STR = os.environ.get("STORAGE_CONN_STR")
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_CONTAINER_NAME") 
OUTPUT_QUEUE_NAME = os.environ.get("STORAGE_QUEUE_NAME")
BLOB_CONTAINER_NAME = "receipt-images"

# Inisialisasi Function App (V2 Model)
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

def upload_image_to_blob(file, filename):
    try:
        blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONN_STR)
        container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)
        
        if not container_client.exists():
            container_client.create_container()

        blob_client = container_client.get_blob_client(filename)
        blob_client.upload_blob(file.stream, overwrite=True)
        return blob_client.url
    except Exception as e:
        logging.error(f"Error uploading blob: {e}")
        return None

# PERHATIKAN: Decorator ini yang mendaftarkan fungsi ke Azure
@app.route(route="transaction/create", methods=["POST"])
def CreateTransaction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing Create Transaction request (V2 Model).')

    try:
        content_type = req.headers.get("Content-Type", "")
        
        user_id = None
        description = None
        amount = 0.0
        lat = None
        lon = None
        image_url = None
        input_type = "text"
        source = "manual_input"

        # --- 1. Parsing Logic ---
        if "application/json" in content_type:
            try:
                req_body = req.get_json()
                user_id = req_body.get("user_id")
                description = req_body.get("description")
                amount = float(req_body.get("amount", 0.0))
                lat = req_body.get("latitude")
                lon = req_body.get("longitude")
                source = req_body.get("source", "manual_input")
                input_type = "text"
            except ValueError:
                pass

        elif "multipart/form-data" in content_type:
            user_id = req.form.get("user_id")
            description = req.form.get("description", "Receipt Upload")
            amount = float(req.form.get("amount", 0.0))
            lat = req.form.get("latitude")
            lon = req.form.get("longitude")
            source = "image_upload"
            input_type = "image"

            if 'image' in req.files:
                file = req.files['image']
                filename = f"{user_id}_{uuid.uuid4()}.jpg"
                uploaded_url = upload_image_to_blob(file, filename)
                if uploaded_url:
                    image_url = uploaded_url
                else:
                    return func.HttpResponse(json.dumps({"error": "Blob upload failed"}), status_code=500)

        # --- 2. Validasi ---
        if not user_id:
            return func.HttpResponse(json.dumps({"error": "Missing user_id"}), status_code=400)

        # --- 3. Reverse Geocoding ---
        location_obj = {"city": "Unknown", "country": "Unknown"}
        if lat and lon:
            try:
                result = rg.search((float(lat), float(lon)))[0]
                location_obj = {"city": result.get("name", "Unknown"), "country": result.get("cc", "Unknown")}
            except Exception:
                pass

        # --- 4. Prepare Document ---
        transaction_id = str(uuid.uuid4())
        document = {
            "id": transaction_id,
            "user_id": user_id,
            "type": "transaction",
            "amount": amount,
            "description": description,
            "transaction_date": datetime.utcnow().isoformat(),
            "image_url": image_url,
            "location": location_obj,
            "category": {"id": "0", "name": "Pending", "category_type": "Uncategorized"},
            "source": source,
            "input_type": input_type,
            "is_processed": False
        }

        # --- 5. Save to Cosmos DB ---
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)
        container.create_item(body=document)

        # --- 6. Send to Queue ---
        queue_client = QueueClient.from_connection_string(STORAGE_CONN_STR, OUTPUT_QUEUE_NAME)
        try:
            queue_client.send_message(json.dumps(document))
        except Exception:
            queue_client.create_queue()
            queue_client.send_message(json.dumps(document))

        return func.HttpResponse(json.dumps({"message": "Success", "data": document}), status_code=201, mimetype="application/json")

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)
    
    
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