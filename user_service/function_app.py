import azure.functions as func
import logging
import json
import os
import jwt
from datetime import datetime
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
from azure.cosmos import CosmosClient, exceptions

app = func.FunctionApp()

# --- KONFIGURASI ---
COSMOS_CONN_STR = os.environ.get("COSMOS_DB_CONN_STR") 
EVENTGRID_ENDPOINT = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
EVENTGRID_KEY = os.getenv("EVENTGRID_ACCESS_KEY")
IS_LOCAL_DEMO = os.getenv("IS_LOCAL_DEMO", "false").lower() == "true"

# Config Database
DB_NAME = "fintrackdb"
CONTAINER_NAME = "item"

# --- HELPER: COSMOS DB CLIENT ---
def get_container():
    if not COSMOS_CONN_STR:
        raise ValueError("COSMOS_DB_CONN_STR belum di-set di Environment Variables!")
    
    client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
    database = client.get_database_client(DB_NAME)
    return database.get_container_client(CONTAINER_NAME)

# --- HELPER: AUTH ---
def _get_user_info_from_token(req: func.HttpRequest) -> dict | None:
    auth_header = req.headers.get('Authorization')
    if not auth_header:
        return None
    try:
        bearer_token = auth_header.split(' ')[1]
    except IndexError:
        return None

    if bearer_token == "debug":
        return {
            "name": "User Debug Cosmos",
            "oid": "user-debug-001", 
            "emails": ["debug@cosmos.com"]
        }

    try:
        decoded_token = jwt.decode(bearer_token, options={"verify_signature": False})
        return decoded_token
    except Exception as e:
        logging.error(f"Token error: {e}")
        return None

# -----------------------------------------------------------------
# FUNGSI 1: Registrasi User (Dengan Validasi Email)
# -----------------------------------------------------------------
@app.route(route="user/register", methods=["POST"])
def UserRegistrationFunction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('UserRegistrationFunction started (Cosmos Mode).')

    try:
        req_body = req.get_json()
        email = req_body.get('email')
        b2c_object_id = req_body.get('b2c_object_id')
        name = req_body.get('name', 'New User')

        if not email or not b2c_object_id:
            return func.HttpResponse(
                json.dumps({"error": "Email and b2c_object_id required"}),
                status_code=400, mimetype="application/json"
            )

        container = get_container()

        # --- 1. VALIDASI EMAIL (PENTING) ---
        # Cek apakah email sudah terdaftar oleh user lain
        try:
            query = "SELECT * FROM c WHERE c.email = @email AND c.type = 'user'"
            params = [{"name": "@email", "value": email}]
            
            existing_users = list(container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))

            if len(existing_users) > 0:
                existing_user = existing_users[0]
                # Jika email ada TAPI id-nya beda, berarti konflik (email sudah dipakai orang lain)
                if existing_user.get("id") != b2c_object_id:
                    logging.warning(f"Conflict: Email {email} is already used by user {existing_user.get('id')}")
                    return func.HttpResponse(
                        json.dumps({"error": f"Email {email} is already registered to another account."}),
                        status_code=409, # 409 Conflict
                        mimetype="application/json"
                    )
                else:
                    logging.info(f"User {b2c_object_id} already exists. Updating profile...")
        
        except Exception as e:
            logging.error(f"Error checking existing email: {e}")
            # Lanjut saja jika query gagal, atau return 500 tergantung kebijakan
            pass

        # --- 2. Siapkan Data User ---
        user_item = {
            "id": b2c_object_id,      # Primary Key
            "user_id": b2c_object_id,
            "type": "user",           # Discriminator
            "email": email,
            "name": name,
            "created_at": datetime.utcnow().isoformat(),
            "profile_completed": True
        }

        # --- 3. Simpan ke Cosmos DB ---
        # upsert_item akan meng-update jika ID sudah ada, atau create baru jika belum
        container.upsert_item(user_item)
        
        logging.info(f"User {b2c_object_id} saved to Cosmos DB.")

        # --- 4. Publish Event ---
        event_data = {
            "id": b2c_object_id,
            "subject": f"User/Created/{b2c_object_id}",
            "data": {
                "user_id": b2c_object_id,
                "email": email,
                "name": name
            },
            "eventType": "User.Created",
            "eventTime": datetime.utcnow().isoformat(),
            "dataVersion": "1.0"
        }

        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'User.Created' skipped.")
        else:
            client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
            client.send([event_data])
            logging.info("Event 'User.Created' published.")

        return func.HttpResponse(
            json.dumps({"message": "User registered successfully", "user_id": b2c_object_id}),
            status_code=201, mimetype="application/json"
        )

    except exceptions.CosmosHttpResponseError as e:
        logging.error(f"Cosmos DB Error: {e.message}")
        return func.HttpResponse(
            json.dumps({"error": "Database Error"}), status_code=500, mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )

# -----------------------------------------------------------------
# FUNGSI 2: Get Profile
# -----------------------------------------------------------------
@app.route(route="user/profile", methods=["GET"])
def GetUserProfileFunction(req: func.HttpRequest) -> func.HttpResponse:
    try:
        user_info = _get_user_info_from_token(req)
        if not user_info:
            return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401)

        user_id = user_info.get("oid") 
        
        container = get_container()
        
        query = "SELECT * FROM c WHERE c.id = @id AND c.type = 'user'"
        params = [{"name": "@id", "value": user_id}]
        
        items = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return func.HttpResponse(
                json.dumps({"error": "User profile not found."}), 
                status_code=404, mimetype="application/json"
            )

        user_entity = items[0]

        profile_data = {
            "user_id": user_entity.get("id"),
            "name": user_entity.get("name"),
            "email": user_entity.get("email"),
            "created_at": user_entity.get("created_at"),
            "source": "cosmos_db_nosql"
        }
        
        return func.HttpResponse(json.dumps(profile_data), status_code=200, mimetype="application/json")

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)