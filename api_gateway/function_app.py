import azure.functions as func
import logging
import json
import os
import requests
import uuid
from datetime import datetime
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
import jwt

app = func.FunctionApp()

# --- KONFIGURASI ---
IS_LOCAL_DEMO = os.getenv("IS_LOCAL_DEMO", "false").lower() == "true"
EVENTGRID_ENDPOINT = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
EVENTGRID_KEY = os.getenv("EVENTGRID_ACCESS_KEY")

JWT_SECRET_KEY = os.getenv("JWT_SECRET") 
JWT_ALGORITHM = "HS256"

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

# ---------------------------------------------------------------------------
# 1. FUNGSI KHUSUS: Report Generation
# Endpoint: POST /report/generate
# ---------------------------------------------------------------------------
@app.route(route="report/generate", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def RequestReportGeneration(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('RequestReportGeneration called.')

    # --- 1. VALIDASI TOKEN & AMBIL USER ID ---
    user_info = _get_user_info_from_token(req)
    
    if not user_info:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized. Token invalid or missing."}), 
            status_code=401, 
            mimetype="application/json"
        )

    # Ambil User ID dari Token (Payload Anda pakai 'user_id')
    user_id = user_info.get("user_id")
    
    if not user_id:
         return func.HttpResponse(
             json.dumps({"error": "Invalid Token: user_id missing"}), 
             status_code=401, 
             mimetype="application/json"
         )
    # -----------------------------------------

    try:
        try:
            req_body = req.get_json()
        except ValueError:
            return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400, mimetype="application/json")

        # user_id TIDAK LAGI diambil dari req_body (Demi Keamanan)
        # user_id = req_body.get('user_id') <--- INI DIHAPUS
        
        year = req_body.get('year')
        
        if not year:
            return func.HttpResponse(
                json.dumps({"error": "Please provide year"}),
                status_code=400,
                mimetype="application/json"
            )

        request_id = str(uuid.uuid4())
        
        # Kirim Event ke Event Grid
        report_request_event = {
            "id": request_id,
            "subject": f"Report/Generation/Request/{user_id}",
            "data": {
                "user_id": user_id, # Ini pakai ID asli dari Token
                "year": year,
                "format": "EXCEL",
                "request_id": request_id
            },
            "eventType": "ReportGeneration.Requested",
            "eventTime": datetime.utcnow().isoformat(),
            "dataVersion": "1.0"
        }

        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'ReportGeneration.Requested' simulated. ID: {request_id}")
        else:
            client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
            client.send([report_request_event])
            logging.info(f"Event sent to Event Grid. ID: {request_id}")

        return func.HttpResponse(
            json.dumps({
                "message": "Permintaan laporan diterima. Kami sedang memprosesnya.",
                "status": "Accepted",
                "request_id": request_id,
                "estimated_time": "1-2 minutes"
            }),
            status_code=202, 
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in RequestReportGeneration: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

# ---------------------------------------------------------------------------
# 2. FUNGSI UMUM: API Gateway Proxy
# ---------------------------------------------------------------------------
@app.route(route="gateway/{*path}", methods=["GET", "POST", "PUT", "DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def gateway(req: func.HttpRequest) -> func.HttpResponse:
    try:
        path = req.route_params.get('path') or ''
        method = req.method
        
        logging.info(f"Gateway proxying request to: {path}")

        # --- 1. SECURITY CHECK (FRONTEND) ---
        # Masukkan endpoint AI ke sini agar lolos pengecekan token
        public_endpoints = [
            "user/login",
            "user/register",
            "ai/language",
            "ai/ocr"
        ]
        
        # Cek Token HANYA JIKA path tidak ada di daftar public
        if path not in public_endpoints:
            user_info = _get_user_info_from_token(req)
            if not user_info:
                return func.HttpResponse(
                    json.dumps({"error": "Unauthorized. Please login first."}), 
                    status_code=401, 
                    mimetype="application/json"
                )
            
            # (Opsional) Inject User ID ke Header untuk service lain
            # req.headers["x-user-id"] = user_info.get("user_id")
        # ------------------------------------

        # --- 2. TENTUKAN TARGET & SISIPKAN BACKEND KEY ---
        target_host = None
        backend_key = None 

        if path.startswith("user"):
            target_host = os.getenv("USER_SERVICE_URL")
        elif path.startswith("transaction"):
            target_host = os.getenv("TRANSACTION_SERVICE_URL")
            backend_key = os.getenv("TRANSACTION_SERVICE_KEY") 
        elif path.startswith("category"):
            target_host = os.getenv("CATEGORY_SERVICE_URL")
        elif path.startswith("ai"):
            target_host = os.getenv("AI_SERVICE_URL")
            # Gateway tetap wajib bawa kunci ini ke Backend AI
            backend_key = os.getenv("AI_SERVICE_KEY") 
        elif path.startswith("report"): 
            target_host = os.getenv("REPORT_SERVICE_URL")
        else:
            return func.HttpResponse(json.dumps({"error": "Unknown path"}), status_code=404)

        if not target_host:
            return func.HttpResponse(json.dumps({"error": "Service configuration missing"}), status_code=500)

        target_url = f"{target_host.rstrip('/')}/{path}"

        # --- 3. SIAPKAN REQUEST ---
        
        # A. Headers
        fwd_headers = {
            k: v for k, v in req.headers.items() 
            if k.lower() not in ['host', 'content-length']
        }

        # B. Inject Function Key (Agar Backend AI mau menerima request dari Gateway)
        if backend_key:
            fwd_headers['x-functions-key'] = backend_key

        # C. Body (Aman untuk JSON & Multipart/File)
        try:
            req_body = req.get_body()
        except:
            req_body = None

        # --- 4. KIRIM REQUEST KE BACKEND ---
        resp = requests.request(
            method=method,
            url=target_url,
            headers=fwd_headers,
            data=req_body,
            params=req.params 
        )
        
        # --- 5. KEMBALIKAN RESPONSE ---
        return func.HttpResponse(
            resp.content,
            status_code=resp.status_code,
            mimetype=resp.headers.get('Content-Type', 'application/json')
        )

    except Exception as e:
        logging.error(f"Gateway Error: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)
    
# ---------------------------------------------------------------------------
# 3. FUNGSI KHUSUS: Cek Status Laporan (Untuk Polling)
# Endpoint: GET /report/status/{request_id}
# ---------------------------------------------------------------------------
@app.route(route="report/status/{request_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def CheckReportStatus(req: func.HttpRequest) -> func.HttpResponse:
    request_id = req.route_params.get('request_id')
    logging.info(f"Gateway: Checking status for {request_id}")

    # --- SECURITY CHECK ---
    # Fungsi ini WAJIB butuh token karena user hanya boleh cek status miliknya sendiri.
    user_info = _get_user_info_from_token(req)
    
    if not user_info:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized. Please login first."}), 
            status_code=401, 
            mimetype="application/json"
        )
    # ----------------------

    try:
        report_service_url = os.getenv("REPORT_SERVICE_URL")
        
        if not report_service_url:
            return func.HttpResponse(json.dumps({"error": "Report Service URL not set"}), status_code=500)

        target_url = f"{report_service_url.rstrip('/')}/report/status/{request_id}"
        
        # Teruskan Header (termasuk Authorization Token yang sudah divalidasi)
        # Token ini nanti akan divalidasi ULANG oleh report_service untuk mengambil user_id
        fwd_headers = {key: value for (key, value) in req.headers.items() if key.lower() != 'host'}

        # Panggil Report Service
        resp = requests.get(target_url, headers=fwd_headers)

        return func.HttpResponse(
            resp.content,
            status_code=resp.status_code,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Gateway Status Check Error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)