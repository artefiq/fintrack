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

    # --- 1. JWT SECURITY CHECK (BARU) ---
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized: Invalid or Missing Token"}), status_code=401)

    # Optional: Kamu bisa memaksa user_id diambil dari token, bukan dari body
    # user_id_from_token = user_info.get("user_id")

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

        # 2. Parsing Input
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

        # 3. Validasi Minimal
        if not user_id:
            return func.HttpResponse(json.dumps({"error": "Missing user_id"}), status_code=400)

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
    
    # --- 1. JWT SECURITY CHECK (BARU) ---
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized: Invalid or Missing Token"}), status_code=401)

    doc_id = req.params.get('id')
    user_id = req.params.get('user_id')

    if not doc_id or not user_id:
        return func.HttpResponse(json.dumps({"error": "Please provide id and user_id"}), status_code=400)

    try:
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)

        item = container.read_item(item=doc_id, partition_key=user_id)
        
        return func.HttpResponse(json.dumps(item), status_code=200, mimetype="application/json")
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": "Transaction not found or error"}), status_code=404)


@app.route(route="admin/dataset/json", methods=["GET"])
def AdminDashboardData(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Fetching Admin Dashboard Data (JSON) with Filters.')

    # --- 1. JWT SECURITY CHECK ---
    user_info = _get_user_info_from_token(req)

    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized or Invalid Token"}), status_code=401)
    
    if user_info.get("role") != "admin":
        return func.HttpResponse(json.dumps({"error": "Access Denied: Admins only"}), status_code=403)

    # --- 2. AMBIL PARAMETER FILTER ---
    start_date = req.params.get('start_date') 
    end_date = req.params.get('end_date')      
    category_filter = req.params.get('category') 

    try:
        client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
        container = client.get_database_client(DATABASE_NAME).get_container_client(CONTAINER_NAME)

        # --- 3. BANGUN QUERY DINAMIS ---
        query = "SELECT * FROM c WHERE 1=1"
        parameters = []

        if start_date and end_date:
            query += " AND c.transaction_date >= @start AND c.transaction_date <= @end"
            parameters.append({"name": "@start", "value": f"{start_date}T00:00:00"})
            parameters.append({"name": "@end", "value": f"{end_date}T23:59:59"})

        if category_filter:
            query += " AND c.category.name = @category"
            parameters.append({"name": "@category", "value": category_filter})

        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        # --- 4. DATA PROCESSING & ANONYMIZATION ---
        dashboard_data = []
        
        for item in items:
            raw_user_id = item.get("user_id", "unknown")
            hashed_user = hashlib.sha256(raw_user_id.encode()).hexdigest()[:8]

            trx_date_str = item.get("transaction_date", "")
            try:
                dt_obj = datetime.fromisoformat(trx_date_str)
                iso_date = dt_obj.isoformat() 
                hour_val = dt_obj.hour
                day_val = dt_obj.strftime("%A")
            except:
                iso_date = trx_date_str
                hour_val = 0
                day_val = "Unknown"

            dashboard_data.append({
                "id": item.get("id"),
                "user_hash": hashed_user,
                "date_full": iso_date,
                "day_name": day_val,
                "hour": hour_val,
                "amount": item.get("amount", 0),
                "description": item.get("description", ""),
                "category": item.get("category", {}).get("name", "Uncategorized"),
                "type": item.get("category", {}).get("category_type", "Expense"),
                "city": item.get("location", {}).get("city", "Unknown"),
                "source": item.get("source", "manual")
            })

        return func.HttpResponse(
            body=json.dumps({
                "message": "Data fetched successfully",
                "filters_applied": {
                    "start": start_date,
                    "end": end_date,
                    "category": category_filter
                },
                "total_rows": len(dashboard_data),
                "data": dashboard_data
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Dashboard Data Error: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)