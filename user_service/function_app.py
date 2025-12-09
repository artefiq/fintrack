import azure.functions as func
import logging
import json
import os
import jwt
import bcrypt
import uuid
from datetime import datetime, timedelta, timezone
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
from azure.cosmos import CosmosClient, exceptions

app = func.FunctionApp()

# --- KONFIGURASI ---
COSMOS_CONN_STR = os.environ.get("COSMOS_DB_CONN_STR") 
EVENTGRID_ENDPOINT = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
EVENTGRID_KEY = os.getenv("EVENTGRID_ACCESS_KEY")
IS_LOCAL_DEMO = os.getenv("IS_LOCAL_DEMO", "false").lower() == "true"

JWT_SECRET_KEY = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# Config Database
DB_NAME = "fintrackdb"
CONTAINER_NAME = "item"

# --- HELPER: COSMOS DB CLIENT ---
def get_container():
    if not COSMOS_CONN_STR:
        raise ValueError("COSMOS_DB_CONN_STR belum di-set!")
    
    client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
    database = client.get_database_client(DB_NAME)
    return database.get_container_client(CONTAINER_NAME)

# --- HELPER: VALIDASI TOKEN ---
def _get_user_info_from_token(req: func.HttpRequest) -> dict | None:
    auth_header = req.headers.get('Authorization')
    if not auth_header:
        return None
    try:
        # Format: "Bearer <token>"
        token = auth_header.split(' ')[1]
        
        # Decode dan validasi signature menggunakan Secret Key kita
        decoded = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return decoded
    except Exception as e:
        logging.error(f"Token invalid: {e}")
        return None

# -----------------------------------------------------------------
# FUNGSI 1: REGISTER (Sign Up Manual)
# -----------------------------------------------------------------
@app.route(route="user/register", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def UserRegister(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Proses Register User Baru...')
    try:
        req_body = req.get_json()
        email = req_body.get('email')
        password = req_body.get('password') # Password polos dari user
        name = req_body.get('name')

        if not email or not password:
            return func.HttpResponse(json.dumps({"error": "Email & Password wajib diisi"}), status_code=400)

        container = get_container()

        # 1. Cek Email Duplikat
        # Query Cosmos untuk cek apakah email sudah ada
        query = "SELECT * FROM c WHERE c.email = @email AND c.type = 'user'"
        params = [{"name": "@email", "value": email}]
        existing = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        
        if existing:
            return func.HttpResponse(json.dumps({"error": "Email sudah terdaftar"}), status_code=409)

        # 2. Hash Password (Agar aman disimpan)
        # bcrypt.hashpw menghasilkan bytes, jadi perlu di-decode ke utf-8 untuk disimpan sebagai string JSON
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # 3. Simpan ke Cosmos DB
        user_id = str(uuid.uuid4())
        user_item = {
            "user_id": user_id,
            "type": "user",
            "email": email,
            "name": name,
            "role": 1,
            "password_hash": hashed_password, # Simpan hash, BUKAN password asli
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        container.upsert_item(user_item)

        # 4. Kirim Event User.Created (Opsional, untuk service lain)
        if not IS_LOCAL_DEMO and EVENTGRID_ENDPOINT:
            try:
                event_data = {
                    "id": user_id,
                    "subject": f"User/Created/{user_id}",
                    "data": {"user_id": user_id, "email": email},
                    "eventType": "User.Created",
                    "eventTime": datetime.now(timezone.utc).isoformat(),
                    "dataVersion": "1.0"
                }
                client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
                client.send([event_data])
            except Exception as e:
                logging.warning(f"Gagal kirim event grid: {e}")

        return func.HttpResponse(json.dumps({"message": "Register Berhasil", "user_id": user_id}), status_code=201)

    except Exception as e:
        logging.error(f"Register Error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)

# -----------------------------------------------------------------
# FUNGSI 2: LOGIN (Sign In Manual) -> Dapat Token JWT
# -----------------------------------------------------------------
@app.route(route="user/login", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def UserLogin(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Proses Login...')
    try:
        req_body = req.get_json()
        email = req_body.get('email')
        password = req_body.get('password')

        if not email or not password:
            return func.HttpResponse(json.dumps({"error": "Email & Password wajib diisi"}), status_code=400)

        container = get_container()

        # 1. Cari User by Email
        query = "SELECT * FROM c WHERE c.email = @email AND c.type = 'user'"
        params = [{"name": "@email", "value": email}]
        users = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

        if not users:
            # User tidak ditemukan
            return func.HttpResponse(json.dumps({"error": "Email atau Password salah"}), status_code=401)

        user = users[0]
        stored_hash = user.get("password_hash")

        # 2. Verifikasi Password
        # Bandingkan password input user dengan hash di DB
        if not stored_hash or not bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
            return func.HttpResponse(json.dumps({"error": "Email atau Password salah"}), status_code=401)

        # 3. Buat Token JWT (Tiket Masuk)
        # Token berlaku 24 jam
        expiration = datetime.now(timezone.utc) + timedelta(hours=24)
        
        payload = {
            "user_id": user["id"],       # ID User (Penting untuk identifikasi di service lain)
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "exp": expiration        # Waktu kadaluarsa
        }
        
        token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        return func.HttpResponse(
            json.dumps({
                "message": "Login Berhasil",
                "token": token,          # <-- Token ini yang dipakai Frontend
                "user_id": user["id"],
                "name": user["name"]
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Login Error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)

# -----------------------------------------------------------------
# FUNGSI 3: GET PROFILE (Butuh Token)
# -----------------------------------------------------------------
@app.route(route="user/profile", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def GetUserProfileFunction(req: func.HttpRequest) -> func.HttpResponse:
    # 1. Validasi Token
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized. Token invalid atau expired."}), status_code=401)

    user_id = user_info.get("user_id")
    
    # 2. Ambil Data Detail dari DB
    try:
        container = get_container()
        query = "SELECT * FROM c WHERE c.id = @id AND c.type = 'user'"
        params = [{"name": "@id", "value": user_id}]
        items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        
        if not items:
            return func.HttpResponse(json.dumps({"error": "User tidak ditemukan"}), status_code=404)

        user_data = items[0]
        
        # Hapus informasi sensitif sebelum dikirim ke frontend
        if "password_hash" in user_data:
            del user_data["password_hash"]

        return func.HttpResponse(json.dumps(user_data), status_code=200, mimetype="application/json")

    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)