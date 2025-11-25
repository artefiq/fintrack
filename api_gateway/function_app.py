import azure.functions as func
import logging
import json
import os
import requests
import uuid
from datetime import datetime
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential

app = func.FunctionApp()

# --- KONFIGURASI ---
IS_LOCAL_DEMO = os.getenv("IS_LOCAL_DEMO", "false").lower() == "true"
EVENTGRID_ENDPOINT = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
EVENTGRID_KEY = os.getenv("EVENTGRID_ACCESS_KEY")

# ---------------------------------------------------------------------------
# 1. FUNGSI KHUSUS: Report Generation
# PERBAIKAN: auth_level=func.AuthLevel.ANONYMOUS (Agar tidak 401)
# ---------------------------------------------------------------------------
@app.route(route="report/generate", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def RequestReportGeneration(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('RequestReportGeneration called.')

    try:
        try:
            req_body = req.get_json()
        except ValueError:
            return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400, mimetype="application/json")

        user_id = req_body.get('user_id')
        year = req_body.get('year')
        
        if not user_id or not year:
            return func.HttpResponse(
                json.dumps({"error": "Please provide user_id and year"}),
                status_code=400,
                mimetype="application/json"
            )

        request_id = str(uuid.uuid4())
        
        report_request_event = {
            "id": request_id,
            "subject": f"Report/Generation/Request/{user_id}",
            "data": {
                "user_id": user_id,
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
# PERBAIKAN: auth_level=func.AuthLevel.ANONYMOUS (Agar tidak 401)
# ---------------------------------------------------------------------------
@app.route(route="gateway/{*path}", methods=["GET", "POST", "PUT", "DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def gateway(req: func.HttpRequest) -> func.HttpResponse:
    try:
        path = req.route_params.get('path') or ''
        method = req.method
        
        logging.info(f"Gateway proxying request to: {path}")

        target = None
        if path.startswith("user"):
            target = os.getenv("USER_SERVICE_URL")
        elif path.startswith("transaction"):
            target = os.getenv("TRANSACTION_SERVICE_URL")
        elif path.startswith("category"):
            target = os.getenv("CATEGORY_SERVICE_URL")
        elif path.startswith("ai"):
            target = os.getenv("AI_SERVICE_URL")
        elif path.startswith("report"): 
            target = os.getenv("REPORT_SERVICE_URL")
        else:
            return func.HttpResponse(
                json.dumps({"error": "Unknown service path"}),
                mimetype="application/json",
                status_code=404
            )

        if not target:
            return func.HttpResponse(
                json.dumps({"error": "Service URL configuration missing"}),
                status_code=500,
                mimetype="application/json"
            )

        fwd_headers = {key: value for (key, value) in req.headers.items() if key.lower() != 'host'}
        target_url = f"{target}/{path}"
        
        req_body = None
        try:
            req_body = req.get_body()
        except:
            pass

        resp = requests.request(
            method=method,
            url=target_url,
            headers=fwd_headers,
            data=req_body
        )
        
        return func.HttpResponse(
            resp.content,
            mimetype=resp.headers.get('Content-Type', 'application/json'),
            status_code=resp.status_code
        )

    except Exception as e:
        logging.error(f"Gateway Error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": f"Gateway Error: {str(e)}"}),
            mimetype="application/json",
            status_code=500
        )