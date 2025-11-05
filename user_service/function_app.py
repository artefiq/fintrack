import azure.functions as func
import logging
import json
import os
import jwt

# --- KONFIGURASI B2C (Nanti diisi di local.settings.json) ---
# isinya didapatkan dari portal Azure AD B2C
B2C_TENANT_NAME = os.getenv("B2C_TENANT_NAME", "your-tenant-name")
B2C_USER_FLOW = os.getenv("B2C_USER_FLOW", "B2C_1_signupsignin1")
B2C_CLIENT_ID = os.getenv("B2C_CLIENT_ID", "your-app-client-id")
B2C_ISSUER_URL = f"https://{B2C_TENANT_NAME}.b2clogin.com/{B2C_TENANT_NAME}.onmicrosoft.com/{B2C_USER_FLOW}/v2.0"
B2C_JWKS_URL = f"https://{B2C_TENANT_NAME}.b2clogin.com/{B2C_TENANT_NAME}.onmicrosoft.com/{B2C_USER_FLOW}/discovery/v2.0/keys"


app = func.FunctionApp()

# -----------------------------------------------------------------
# HELPER: FUNGSI VALIDASI TOKEN B2C
# -----------------------------------------------------------------
def _get_user_info_from_token(req: func.HttpRequest) -> dict | None:
    """
    Membaca header 'Authorization', memvalidasi token B2C, 
    dan mengembalikan 'claims' (info user) jika valid.
    Mengembalikan None jika tidak valid atau tidak ada token.
    """
    logging.info("Memvalidasi token...")
    
    # 1. Ambil token dari header
    auth_header = req.headers.get('Authorization')
    if not auth_header:
        logging.warning("Request tidak memiliki 'Authorization' header.")
        return None

    try:
        # Header harus 'Bearer <token>'
        bearer_token = auth_header.split(' ')[1]
    except IndexError:
        logging.warning("Format 'Authorization' header salah. Harus 'Bearer <token>'.")
        return None

    # --- DEBUG ---
    # Jika token adalah "debug", kembalikan data user palsu
    # Ini agar  bisa tes lokal tanpa B2C
    if bearer_token == "debug":
        logging.warning("--- MENGGUNAKAN TOKEN DEBUG LOKAL ---")
        return {
            "name": "User Debug",
            "oid": "debug-user-id-12345", # Object ID (User ID unik dari B2C)
            "emails": ["debug@email.com"]
        }
    # --- AKHIR DEBUG ---


    # --- LOGIKA VALIDASI B2C (Untuk Cloud) ---
    # TODO: Implementasikan validasi JWT penuh di sini
    # 1. Ambil JWKS (public keys) dari B2C_JWKS_URL
    # 2. Dapatkan public key yang benar dari token
    # 3. Gunakan jwt.decode() dengan public key tersebut
    # 4. Verifikasi 'aud' (harus B2C_CLIENT_ID)
    # 5. Verifikasi 'iss' (harus B2C_ISSUER_URL)
    # 6. Verifikasi 'exp' (expiry time)
    
    # Contoh (sederhana, tanpa validasi signature):
    try:
        # Hanya untuk demo membaca claims
        decoded_token = jwt.decode(bearer_token, options={"verify_signature": False})
        
        # TODO: Validasi 'aud', 'iss', dan 'exp' di sini
        
        logging.info("Token berhasil divalidasi (tanpa signature).")
        return decoded_token # 'decoded_token' berisi semua info user
        
    except jwt.InvalidTokenError as e:
        logging.error(f"Token tidak valid: {str(e)}")
        return None
    except Exception as e:
        logging.error(f"Error saat decode token: {str(e)}")
        return None

# -----------------------------------------------------------------
# FUNGSI 1: Registrasi User
# -----------------------------------------------------------------
@app.route(route="user/register", methods=["POST"])
def UserRegistrationFunction(req: func.HttpRequest) -> func.HttpResponse:
    """
    Fungsi ini (harusnya) dipanggil OLEH B2C (via custom policy)
    atau oleh client app setelah B2C berhasil membuat user
    yang berfungsi untuk 'menyinkronkan' user B2C ke database lokal.
    """
    logging.info('UserRegistrationFunction memproses sebuah request.')

    try:
        req_body = req.get_json()
        email = req_body.get('email')
        b2c_object_id = req_body.get('b2c_object_id') # ID unik dari B2C

        if not email or not b2c_object_id:
            return func.HttpResponse(
                json.dumps({"error": "Email dan b2c_object_id dibutuhkan."}),
                mimetype="application/json",
                status_code=400
            )

        # 2. TODO: Simpan user ke Database
        #    Simpan 'email' dan 'b2c_object_id' ke tabel User 
        logging.info(f"Menyinkronkan user baru ke DB: {email} (ID: {b2c_object_id})")
        
        # 3. TODO: Publish event 'User.Created' ke Event Grid
        logging.info("Menerbitkan event User.Created...")

        # 4. Kirim respons sukses
        response_data = {
            "message": "User berhasil disinkronkan",
            "user": { "email": email, "b2c_object_id": b2c_object_id }
        }
        
        return func.HttpResponse(
            json.dumps(response_data),
            mimetype="application/json",
            status_code=201 # 201 Created
        )

    except Exception as e:
        logging.error(f"Error di UserRegistrationFunction: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": f"Terjadi kesalahan internal server: {str(e)}"}),
            mimetype="application/json",
            status_code=500
        )


# -----------------------------------------------------------------
# FUNGSI 2: Ambil Profil User
# -----------------------------------------------------------------
@app.route(route="user/profile", methods=["GET"])
def GetUserProfileFunction(req: func.HttpRequest) -> func.HttpResponse:
    """
    Fungsi yang diamankan. Hanya user yang terotentikasi (dengan token valid)
    yang bisa mengakses ini.
    """
    logging.info('GetUserProfileFunction memproses sebuah request.')
    
    # 1. Validasi token dan dapatkan info user
    user_info = _get_user_info_from_token(req)
    
    if user_info is None:
        # Jika token tidak ada atau tidak valid
        return func.HttpResponse(
            json.dumps({"error": "Otentikasi dibutuhkan. Token tidak valid atau tidak ada."}),
            mimetype="application/json",
            status_code=401 # 401 Unauthorized
        )

    # 2. Sudah punya info user yang valid (dari token)
    user_id = user_info.get("oid") 
    user_name = user_info.get("name", "Pengguna")

    # 3. TODO: Ambil data profil lengkap dari Database 
    #    Gunakan 'user_id' (oid) untuk query ke tabel User
    logging.info(f"Mengambil profil dari DB untuk user ID: {user_id}")
    
    # (db palsu)
    db_profile_data = {
        "user_id": user_id,
        "name": user_name,
        "email": user_info.get("emails", ["email-tidak-ditemukan"])[0],
        "join_date": "2025-01-01T10:00:00Z",
        "last_login": "2025-11-05T03:00:00Z"
    }

    # 4. Kirim respons sukses
    return func.HttpResponse(
        json.dumps(db_profile_data),
        mimetype="application/json",
        status_code=200
    )

