import azure.functions as func
import logging
import json
import uuid
import os
import hashlib
import jwt # Pastikan library PyJWT terinstall
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

# Konfigurasi JWT
JWT_SECRET_KEY = os.environ.get("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# Inisialisasi Function App (V2 Model)
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# --- HELPER FUNCTIONS ---

def _get_user_info_from_token(req: func.HttpRequest) -> dict | None:
    """
    Validasi Token JWT dan return payloadnya.
    """
    auth_header = req.headers.get('Authorization')
    if not auth_header:
        return None
    
    try:
        parts = auth_header.split(' ')
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return None
        
        token = parts[1]
        decoded = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return decoded
    except Exception as e:
        logging.error(f"Token invalid: {e}")
        return None

def upload_image_to_blob(file, filename):
    """
    Upload file gambar ke Azure Blob Storage
    """
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

# --- MAIN FUNCTIONS ---

@app.route(route="transaction/create", methods=["POST"])
def CreateTransaction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing Create Transaction request.')

    # --- 1. JWT SECURITY CHECK ---
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized: Invalid or Missing Token"}), status_code=401)
    
    # Ambil user_id dari token
    user_id = user_info.get("user_id")
    if not user_id:
         return func.HttpResponse(json.dumps({"error": "Invalid Token Payload: Missing user_id"}), status_code=401)

    try:
        content_type = req.headers.get("Content-Type", "")
        
        # Default Variables
        description = None
        amount = 0.0
        lat = None
        lon = None
        image_url = None
        input_type = "text"
        source = "Cash"

        # 2. Parsing Input (JSON vs Multipart)
        if "application/json" in content_type:
            try:
                req_body = req.get_json()
                # Text Input: Description WAJIB, Amount OPSIONAL (Default 0.0)
                description = req_body.get("description") 
                amount = float(req_body.get("amount", 0.0)) # Default 0 kalau tidak dikirim
                lat = req_body.get("latitude")
                lon = req_body.get("longitude")
                source = req_body.get("source", "Cash")
                input_type = "text"
            except ValueError:
                pass

        elif "multipart/form-data" in content_type:
            # Image Input: Description & Amount OPSIONAL (Default None & 0.0)
            description = req.form.get("description") # Bisa kosong/None
            amount = float(req.form.get("amount", 0.0)) # Default 0
            lat = req.form.get("latitude")
            lon = req.form.get("longitude")
            source = req.form.get("source", "Cash")
            input_type = "image"

            if 'image' in req.files:
                file = req.files['image']
                filename = f"{user_id}_{uuid.uuid4()}.jpg"
                uploaded_url = upload_image_to_blob(file, filename)
                if uploaded_url:
                    image_url = uploaded_url
                else:
                    return func.HttpResponse(json.dumps({"error": "Blob upload failed"}), status_code=500)
            else:
                 return func.HttpResponse(json.dumps({"error": "Image file is required for multipart request"}), status_code=400)

        # 3. Validasi Logika
        # Jika Input Text: Description TIDAK BOLEH kosong (karena AI butuh teks buat mikir)
        if input_type == "text" and not description:
             return func.HttpResponse(json.dumps({"error": "Missing description for text input"}), status_code=400)

        # Jika Input Image: Description BOLEH kosong.
        # Kita kasih placeholder biar di database field-nya ada isinya (rapi).
        if input_type == "image" and not description:
            description = "Pending Scan" # Nanti AI/OCR yang ganti tulisan ini

        # 4. Reverse Geocoding
        location_obj = {"city": "Unknown", "country": "Unknown"}
        if lat and lon:
            try:
                result = rg.search((float(lat), float(lon)))[0]
                location_obj = {"city": result.get("name", "Unknown"), "country": result.get("cc", "Unknown")}
            except Exception:
                pass

        # 5. Prepare Document
        transaction_id = str(uuid.uuid4())
        document = {
            "id": transaction_id,
            "user_id": user_id, # Dari Token
            "type": "transaction",
            "amount": amount,       # Bisa 0.0
            "description": description, # Bisa "Pending Scan"
            "transaction_date": datetime.utcnow().isoformat(),
            "image_url": image_url,
            "location": location_obj,
            "category": {"id": "0", "name": "Pending", "category_type": "Uncategorized"},
            "source": source,
            "input_type": input_type,
            "is_processed": False
        }

        # 6. Save to Cosmos DB
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)
        container.create_item(body=document)

        # 7. Send to Queue
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
    
    # --- 1. JWT SECURITY CHECK ---
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized: Invalid or Missing Token"}), status_code=401)

    # --- 2. AMBIL USER_ID DARI TOKEN (SECURE) ---
    # Kita abaikan req.params.get('user_id') agar user tidak bisa memalsukan identitas
    user_id = user_info.get("user_id")

    if not user_id:
         return func.HttpResponse(json.dumps({"error": "Invalid Token Payload: Missing user_id"}), status_code=401)

    # --- 3. AMBIL TRANSACTION ID DARI PARAM ---
    doc_id = req.params.get('id')

    if not doc_id:
        return func.HttpResponse(json.dumps({"error": "Please provide transaction id"}), status_code=400)

    try:
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)

        # BACA ITEM
        # Kuncinya di sini: partition_key=user_id (dari token).
        # Kalau doc_id tersebut milik orang lain, Cosmos DB akan menganggapnya "Tidak Ada" (404)
        item = container.read_item(item=doc_id, partition_key=user_id)
        
        return func.HttpResponse(json.dumps(item), status_code=200, mimetype="application/json")
    except Exception as e:
        # Error biasanya karena 404 Not Found (Entah ID salah, atau ID benar tapi punya orang lain)
        return func.HttpResponse(json.dumps({"error": "Transaction not found"}), status_code=404)

@app.route(route="transaction/list", methods=["GET"])
def GetUserTransactions(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Fetching User Transaction History.')

    # --- 1. JWT SECURITY CHECK ---
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401)

    # Ambil user_id dari token
    user_id = user_info.get("user_id")
    if not user_id:
         return func.HttpResponse(json.dumps({"error": "Invalid Token"}), status_code=401)

    try:
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        container = client.get_database_client(DATABASE_NAME).get_container_client(CONTAINER_NAME)

        # --- 2. QUERY COSMOS DB ---
        # Filter: user_id AND type='transaction'
        # Sort: transaction_date DESC (Terbaru diatas)
        query = """
            SELECT * FROM c 
            WHERE c.user_id = @userId 
            AND c.type = 'transaction' 
            ORDER BY c.transaction_date DESC
        """
        
        parameters = [
            {"name": "@userId", "value": user_id}
        ]

        # Eksekusi Query
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=False 
        ))

        # --- 3. DATA PROCESSING (LENGKAP) ---
        history_data = []
        for item in items:
            history_data.append({
                "id": item.get("id"),
                "type": item.get("type"), # transaction
                "amount": item.get("amount", 0),
                "description": item.get("description"),
                "transaction_date": item.get("transaction_date"),
                "image_url": item.get("image_url"),
                
                # Objek Nested (Location & Category) dikirim utuh
                "location": item.get("location", {}), 
                "category": item.get("category", {}), 
                
                "source": item.get("source", "cash"),
                "input_type": item.get("input_type", "text"),
                "is_processed": item.get("is_processed", False),
                "ai_confidence": item.get("ai_confidence") # Tambahan data dari AI
            })

        return func.HttpResponse(
            body=json.dumps({
                "message": "Success",
                "total_rows": len(history_data),
                "data": history_data
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"History Error: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)