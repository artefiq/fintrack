import logging
import azure.functions as func
import json
import uuid
import os
from datetime import datetime
import reverse_geocoder as rg 
from azure.cosmos import CosmosClient
from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient

# Konfigurasi Environment Variable
COSMOS_CONN_STR = os.environ.get("COSMOS_CONN_STR")
STORAGE_CONN_STR = os.environ.get("STORAGE_CONN_STR")# Dipake buat Queue & Blob
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_CONTAINER_NAME")
OUTPUT_QUEUE_NAME = os.environ.get("STORAGE_QUEUE_NAME")

BLOB_CONTAINER_NAME = "receipt-images" # Nama container khusus buat simpan gambar

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

def upload_image_to_blob(file, filename):
    """
    Helper function untuk upload gambar ke Azure Blob Storage
    """
    try:
        blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONN_STR)
        
        # Pake variable BLOB_CONTAINER_NAME (bukan CONTAINER_NAME)
        container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)
        
        # Buat container kalau belum ada
        if not container_client.exists():
            container_client.create_container()

        blob_client = container_client.get_blob_client(filename)
        blob_client.upload_blob(file.stream, overwrite=True)
        
        return blob_client.url
    except Exception as e:
        logging.error(f"Error uploading blob: {e}")
        return None

@app.route(route="transaction/create", methods=["POST"])
def CreateTransaction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing Create Transaction request (Unified Version).')

    try:
        content_type = req.headers.get("Content-Type", "")
        
        # Default Variables
        user_id = None
        description = None
        amount = 0.0
        lat = None
        lon = None
        image_url = None
        input_type = "text"
        source = "manual_input"

        # --- 1. PARSING INPUT (JSON vs MULTIPART) ---
        
        # SKENARIO A: Input Text Biasa (JSON)
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

        # SKENARIO B: Input Image Upload (Multipart)
        elif "multipart/form-data" in content_type:
            user_id = req.form.get("user_id")
            description = req.form.get("description", "Receipt Upload")
            amount = float(req.form.get("amount", 0.0)) # Biasanya 0 kalau scan struk
            lat = req.form.get("latitude")
            lon = req.form.get("longitude")
            source = "image_upload"
            input_type = "image"

            # Proses File Gambar
            if 'image' in req.files:
                file = req.files['image']
                # Rename file biar unik: userid_uuid.jpg
                filename = f"{user_id}_{uuid.uuid4()}.jpg" 
                
                # Upload function call
                uploaded_url = upload_image_to_blob(file, filename)
                
                if uploaded_url:
                    image_url = uploaded_url
                else:
                    raise Exception("Failed to upload image to Blob Storage")
            else:
                 return func.HttpResponse(
                    json.dumps({"error": "No image file provided in multipart form"}),
                    status_code=400, mimetype="application/json"
                )

        # --- 2. VALIDASI MINIMAL ---
        # Description boleh default kalau image, tapi user_id wajib
        if not user_id:
            return func.HttpResponse(
                json.dumps({"error": "Missing user_id"}),
                status_code=400, mimetype="application/json"
            )
        
        if not description and input_type == "text":
             return func.HttpResponse(
                json.dumps({"error": "Missing description"}),
                status_code=400, mimetype="application/json"
            )

        # --- 3. PROSES REVERSE GEOCODER ---
        location_obj = {
            "city": "Unknown",
            "country": "Unknown"
        }

        if lat and lon:
            try:
                # Convert ke float karena dari req.form biasanya string
                lat_float = float(lat)
                lon_float = float(lon)
                result = rg.search((lat_float, lon_float))[0]
                location_obj = {
                    "city": result.get("name", "Unknown"),
                    "country": result.get("cc", "Unknown")
                }
                logging.info(f"Reverse geocode result: {location_obj}")
            except Exception as e:
                logging.error(f"Reverse geocoder failed: {str(e)}")

        # --- 4. BENTUK DOKUMEN NoSQL ---
        transaction_id = str(uuid.uuid4())
        
        document = {
            "id": transaction_id,
            "user_id": user_id,
            "type": "transaction",
            
            "amount": amount,
            "description": description,
            "transaction_date": datetime.utcnow().isoformat(),
            
            # Field baru utk image
            "image_url": image_url, 

            "location": location_obj,

            "category": {
                "id": "0",
                "name": "Pending Categorization",
                "category_type": "Uncategorized"
            },
            
            "source": source,
            "input_type": input_type,
            "ai_confidence": "0.0",
            "is_processed": False 
        }

        # --- 5. SIMPAN KE COSMOS DB ---
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        # Disini kita pakai CONTAINER_NAME (punya Cosmos)
        container = database.get_container_client(CONTAINER_NAME) 
        
        container.create_item(body=document)

        # --- 6. KIRIM KE QUEUE ---
        queue_client = QueueClient.from_connection_string(STORAGE_CONN_STR, OUTPUT_QUEUE_NAME)
        message_content = json.dumps(document)
        
        try:
            queue_client.send_message(message_content)
        except Exception:
            queue_client.create_queue()
            queue_client.send_message(message_content)

        # --- 7. RESPON SUKSES ---
        return func.HttpResponse(
            json.dumps({
                "message": "Transaction created successfully",
                "id": transaction_id,
                "data": document, 
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