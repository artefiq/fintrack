import azure.functions as func
import logging
import json
import os
import uuid
from datetime import datetime, timezone
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
from azure.cosmos import CosmosClient, exceptions
from azure.storage.blob import BlobServiceClient
import pandas as pd
from io import BytesIO
import jwt

app = func.FunctionApp()

# --- KONFIGURASI ---
IS_LOCAL_DEMO = os.getenv("IS_LOCAL_DEMO", "false").lower() == "true"

# 1. Cosmos DB Config (NoSQL)
COSMOS_CONN_STR = os.getenv("COSMOS_DB_CONN_STR")
DB_NAME = "fintrackdb"
CONTAINER_NAME = "item"

# 2. Blob Config (Tetap butuh Storage Account biasa untuk simpan file PDF/Excel)
BLOB_CONN_STR = os.getenv("AZURE_BLOB_CONN_STR") 

# 3. Event Grid Config
EVENTGRID_ENDPOINT = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
EVENTGRID_KEY = os.getenv("EVENTGRID_ACCESS_KEY")

JWT_SECRET_KEY = os.getenv("JWT_SECRET") 
JWT_ALGORITHM = "HS256"

# --- HELPER: VALIDASI TOKEN MANDIRI ---
def _get_user_info_from_token(req: func.HttpRequest) -> dict | None:
    auth_header = req.headers.get('Authorization')
    if not auth_header:
        return None
    try:
        # Format: "Bearer <token>"
        token = auth_header.split(' ')[1]
        
        # Decode dan validasi signature menggunakan Secret Key yang sama dengan User Service
        # Kita tidak perlu memanggil User Service, cukup validasi matematik di sini.
        if not JWT_SECRET_KEY:
             logging.error("JWT_SECRET missing in configuration")
             return None
             
        decoded = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return decoded
    except Exception as e:
        logging.error(f"Token invalid: {e}")
        return None

# --- HELPER: DB CLIENT ---
def get_container():
    if not COSMOS_CONN_STR:
        raise ValueError("COSMOS_DB_CONN_STR is missing")
    
    client = CosmosClient.from_connection_string(COSMOS_CONN_STR)
    database = client.get_database_client(DB_NAME)
    return database.get_container_client(CONTAINER_NAME)

# -----------------------------------------------------------------
# FUNGSI 1: GenerateReportFunction (Harian)
# -----------------------------------------------------------------
@app.event_grid_trigger(arg_name="event")
def GenerateReportFunction(event: func.EventGridEvent):
    logging.info(f"GenerateReportFunction dipicu: {event.event_type}")
    try:
        data = event.get_json()
        user_id = data.get("user_id")
        transactions = data.get("transactions", [])

        # Hitung total
        total_income = sum(t["amount"] for t in transactions if t["type"] == "income")
        total_expense = sum(t["amount"] for t in transactions if t["type"] == "expense")
        savings = total_income - total_expense

        # Simpan ke Cosmos DB (Single Container)
        container = get_container()

        new_report = {
            "id": str(uuid.uuid4()),       # Wajib di Cosmos
            "type": "report",              # Discriminator (PENTING)
            "report_type": "daily",
            "user_id": user_id,
            "total_income": total_income,
            "total_expense": total_expense,
            "savings": savings,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "storage_path": None
        }
        
        container.upsert_item(new_report)
        logging.info(f"Laporan harian user {user_id} tersimpan di Cosmos DB.")

        # Publish Event
        report_event_data = {
            "id": event.id,
            "subject": f"Report/Updated/{user_id}",
            "data": new_report,
            "eventType": "Report.Updated",
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "dataVersion": "1.0"
        }

        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'Report.Updated' skipped.")
        else:
            client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
            client.send([report_event_data])
            logging.info("Event published.")

    except Exception as e:
        logging.error(f"Error di GenerateReportFunction: {e}")
        raise e

# -----------------------------------------------------------------
# FUNGSI 2: MonthlyReportSchedulerFunction (Timer)
# -----------------------------------------------------------------
@app.schedule(schedule="0 0 0 1 * *", arg_name="mytimer", run_on_startup=False) 
def MonthlyReportSchedulerFunction(mytimer: func.TimerRequest) -> None:
    utc_timestamp = datetime.now(timezone.utc).isoformat()
    logging.info(f"Monthly Scheduler berjalan: {utc_timestamp}")

    try:
        event_data = {
            "id": f"month-ended-{uuid.uuid4()}",
            "subject": "Month/Ended",
            "data": {"month": datetime.now(timezone.utc).strftime("%Y-%m")},
            "eventType": "Month.Ended",
            "eventTime": utc_timestamp,
            "dataVersion": "1.0"
        }

        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'Month.Ended' skipped.")
        else:
            client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
            client.send([event_data])
            logging.info("Event 'Month.Ended' published.")

    except Exception as e:
        logging.error(f"Error Scheduler: {e}")
        raise e

# -----------------------------------------------------------------
# FUNGSI 3: OnMonthEndedFunction (Bulanan)
# -----------------------------------------------------------------
@app.event_grid_trigger(arg_name="event")
def OnMonthEndedFunction(event: func.EventGridEvent):
    logging.info(f"OnMonthEndedFunction dipicu: {event.event_type}")
    try:
        if event.event_type != "Month.Ended":
            return 

        month = event.get_json().get("month")
        logging.info(f"Generating monthly report for {month}")

        container = get_container()

        # 1. Ambil User Unik dari Transaksi di Cosmos
        # Query SQL Cosmos DB: Ambil distinct user_id dari item tipe 'transaction'
        query_users = "SELECT DISTINCT VALUE c.user_id FROM c WHERE c.type = 'transaction'"
        user_ids = list(container.query_items(query=query_users, enable_cross_partition_query=True))

        for user_id in user_ids:
            logging.info(f"Processing user {user_id}...")
            
            total_income = 0.0
            total_expense = 0.0
            
            # 2. Query Transaksi User Ini
            # Kita filter by user_id dan type='transaction'
            query_trans = "SELECT * FROM c WHERE c.type = 'transaction' AND c.user_id = @user_id"
            params = [{"name": "@user_id", "value": user_id}]
            
            user_trans = container.query_items(
                query=query_trans, parameters=params, enable_cross_partition_query=True
            )

            for t in user_trans:
                try:
                    trans_date = datetime.fromisoformat(t["transaction_date"])
                    if trans_date.strftime("%Y-%m") == month:
                        if t.get("category_id") == 2: 
                            total_income += t["amount"]
                        else:
                            total_expense += t["amount"]
                except:
                    continue

            savings = total_income - total_expense

            # 3. Simpan Laporan Bulanan
            new_report = {
                "id": str(uuid.uuid4()),
                "type": "report",          # Discriminator
                "report_type": "monthly",
                "user_id": user_id,
                "month": month,
                "total_income": total_income,
                "total_expense": total_expense,
                "savings": savings,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            container.upsert_item(new_report)

        # Publish Event
        report_gen_event = {
            "id": f"report-generated-{month}",
            "subject": f"Report/Generated/{month}",
            "data": {"month": month},
            "eventType": "Report.Generated",
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "dataVersion": "1.0"
        }

        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'Report.Generated' skipped.")
        else:
            client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
            client.send([report_gen_event])
            logging.info("Event published.")

    except Exception as e:
        logging.error(f"Error OnMonthEnded: {e}")
        raise e

# -----------------------------------------------------------------
# FUNGSI 4: PdfGeneratorFunction (Heavy Workload - PDF/Excel)
# -----------------------------------------------------------------
@app.event_grid_trigger(arg_name="event")
def PdfGeneratorFunction(event: func.EventGridEvent):
    logging.info(f"PdfGeneratorFunction start: {event.event_type}")

    try:
        if event.event_type != "ReportGeneration.Requested":
            return

        event_data = event.get_json()
        user_id = event_data.get("user_id")
        year = str(event_data.get("year")) # Pastikan string
        req_id = event_data.get("request_id")

        logging.info(f"Cek Permintaan: Report {year} User {user_id}")

        container = get_container()

        check_query = """
            SELECT VALUE COUNT(1) 
            FROM c 
            WHERE c.type = 'transaction' 
            AND c.user_id = @user_id 
            AND c.is_processed = false
            AND STARTSWITH(c.transaction_date, @year)
        """
        check_params = [
            {"name": "@user_id", "value": str(user_id)},
            {"name": "@year", "value": year}
        ]

        # Eksekusi Query Ringan
        pending_count = list(container.query_items(
            query=check_query, parameters=check_params, enable_cross_partition_query=True
        ))[0]

        if pending_count > 0:
            msg = f"GAGAL: Masih ada {pending_count} transaksi yang belum selesai dikategorikan AI."
            logging.warning(msg)
            
            failure_event = {
                "id": str(uuid.uuid4()),
                "subject": f"Report/Generation/Failed/{user_id}",
                "data": {
                    "user_id": user_id,
                    "status": "FAILED",
                    "reason": "AI_PROCESSING_PENDING",
                    "message": "Mohon tunggu, AI sedang mengkategorikan transaksi Anda."
                },
                "eventType": "ReportGeneration.Failed",
                "eventTime": datetime.now(timezone.utc).isoformat(),
                "dataVersion": "1.0"
            }
            
            if not IS_LOCAL_DEMO:
                client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
                client.send([failure_event])
            
            return # BERHENTI DI SINI (JANGAN LANJUT GENERATE)
        
        logging.info("✅ Data bersih. Semua transaksi sudah diproses. Lanjut generate...")
        # ==============================================================================


        # 1. Query Data Transaksi (Lanjut proses normal)
        query = "SELECT * FROM c WHERE c.type = 'transaction' AND c.user_id = @user_id"
        params = [{"name": "@user_id", "value": str(user_id)}]
        
        user_trans = list(container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        ))
        
        data_list = []
        for t in user_trans:
            try:
                trans_date = datetime.fromisoformat(t["transaction_date"])
                if str(trans_date.year) == year:

                    cat_name = "Uncategorized"
                    cat_type = "Expense"

                    if "category" in t and isinstance(t["category"], dict):
                        cat_name = t["category"].get("name", "Uncategorized")
                        cat_type = t["category"].get("category_type", "Expense")
                        
                    data_list.append({
                        "Date": t["transaction_date"],
                        "Description": t["description"],
                        "Amount": t["amount"],
                        "Category": cat_name,
                        "Type": cat_type
                    })
            except:
                continue

        if not data_list:
            logging.warning("Data kosong.")
            # Bisa kirim event failed juga disini jika mau
            return

        # 2. Proses Pandas
        df = pd.DataFrame(data_list)
        pivot_summary = df.pivot_table(
            index=["Type", "Category"], 
            values="Amount", 
            aggfunc="sum"
        )
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Transactions', index=False)
            pivot_summary.to_excel(writer, sheet_name='Summary')

            for sheet in writer.sheets.values():
                for column in sheet.columns:
                    sheet.column_dimensions[column[0].column_letter].width = 20
        
        output.seek(0)
        file_content = output.getvalue()

        # 3. Upload Blob
        if not BLOB_CONN_STR:
            raise ValueError("AZURE_BLOB_CONN_STR missing for file upload")

        blob_service_client = BlobServiceClient.from_connection_string(BLOB_CONN_STR)
        container_name = "reports"
        
        try:
            container_client = blob_service_client.create_container(container_name)
        except Exception:
            container_client = blob_service_client.get_container_client(container_name)

        blob_name = f"report_{user_id}_{year}_{req_id}.xlsx"
        blob_client = container_client.get_blob_client(blob_name)
        
        blob_client.upload_blob(file_content, overwrite=True)
        blob_url = blob_client.url

        logging.info(f"Upload Sukses: {blob_url}")

        # 4. Simpan Metadata ke CosmosDB
        try:
            report_record = {
                "id": req_id,
                "type": "annual_report_file",
                "user_id": user_id,
                "year": year,
                "file_url": blob_url,
                "status": "COMPLETED",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            container.upsert_item(report_record)
            logging.info(f"Metadata laporan disimpan ke Cosmos DB dengan ID: {req_id}")

        except Exception as db_error:
            logging.error(f"Gagal menyimpan metadata ke Cosmos DB: {db_error}")

        # 5. Publish Completion Event
        completion_event = {
            "id": str(uuid.uuid4()),
            "subject": f"Report/Generation/Completed/{user_id}",
            "data": {
                "user_id": user_id,
                "status": "COMPLETED",
                "download_url": blob_url,
                "message": "Laporan tahunan Anda siap diunduh."
            },
            "eventType": "ReportGeneration.Completed",
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "dataVersion": "1.0"
        }

        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'ReportGeneration.Completed' skipped.")
        else:
            client = EventGridPublisherClient(EVENTGRID_ENDPOINT, AzureKeyCredential(EVENTGRID_KEY))
            client.send([completion_event])
            logging.info("Event published.")

    except Exception as e:
        logging.error(f"Error PdfGenerator: {e}")
        raise e
    
# -----------------------------------------------------------------
# FUNGSI 5: GetReportStatusFunction (Untuk Polling Frontend)
# Endpoint: GET /report/status/{request_id}
# -----------------------------------------------------------------
@app.route(route="report/status/{request_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def GetReportStatusFunction(req: func.HttpRequest) -> func.HttpResponse:
    request_id = req.route_params.get('request_id')
    logging.info(f"Cek status laporan ID: {request_id}")

    if not request_id:
        return func.HttpResponse(json.dumps({"error": "Request ID missing"}), status_code=400)

    # --- 1. VALIDASI TOKEN & AMBIL USER ID ---
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")

    # Ambil User ID dari Token (Payload Anda pakai 'user_id')
    current_user_id = user_info.get("user_id")
    
    if not current_user_id:
         return func.HttpResponse(json.dumps({"error": "Invalid Token: user_id missing"}), status_code=401, mimetype="application/json")
    # -----------------------------------------

    try:
        container = get_container()
        
        # --- 2. QUERY DENGAN KEAMANAN (FILTER USER_ID) ---
        # Kita tambahkan AND c.user_id = @user_id
        # Supaya user A tidak bisa melihat status laporan user B walau tahu ID-nya
        query = """
            SELECT * FROM c 
            WHERE c.id = @id 
            AND c.user_id = @user_id 
            AND (c.type = 'annual_report_file' OR c.type = 'report_file')
        """
        
        params = [
            {"name": "@id", "value": request_id},
            {"name": "@user_id", "value": current_user_id} # Parameter tambahan
        ]
        
        items = list(container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        ))

        if not items:
            # Jika tidak ketemu, bisa jadi:
            # 1. Memang belum selesai diproses (Processing)
            # 2. ID salah
            # 3. ID benar tapi punya orang lain (Security)
            # Kita return 202 Accepted agar aman & konsisten
            return func.HttpResponse(
                json.dumps({
                    "status": "PROCESSING",
                    "message": "Laporan sedang dibuat atau ID tidak ditemukan..."
                }),
                status_code=202, 
                mimetype="application/json"
            )

        report = items[0]
        
        # Jika statusnya FAILED
        if report.get("status") == "FAILED":
            return func.HttpResponse(
                json.dumps({
                    "status": "FAILED",
                    "reason": report.get("reason"),
                    "message": report.get("message", "Gagal membuat laporan.")
                }),
                status_code=400,
                mimetype="application/json"
            )

        # Jika sudah COMPLETED
        return func.HttpResponse(
            json.dumps({
                "status": "COMPLETED",
                "file_url": report.get("file_url"),
                "year": report.get("year"),
                "generated_at": report.get("created_at")
            }),
            status_code=200, 
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error GetReportStatus: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
    
# -----------------------------------------------------------------
# FUNGSI 6: GetReportHistoryFunction (Riwayat Laporan)
# Endpoint: GET /report/history
# -----------------------------------------------------------------
@app.route(route="report/history", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def GetReportHistoryFunction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Mengambil riwayat laporan user...")

    # 1. Validasi Token
    user_info = _get_user_info_from_token(req)
    if not user_info:
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")

    # 2. Ambil User ID
    user_id = user_info.get("user_id") 
    
    if not user_id:
        return func.HttpResponse(json.dumps({"error": "Invalid Token Data: user_id missing"}), status_code=401, mimetype="application/json")
    try:
        container = get_container() # Helper yang sudah ada (konek ke fintrackdb -> item)
        
        # 3. Query Cosmos DB
        # Cari semua dokumen dengan type='report_file' ATAU 'annual_report_file' milik user ini
        # Urutkan dari yang terbaru (created_at DESC)
        query = """
            SELECT * FROM c 
            WHERE (c.type = 'annual_report_file' OR c.type = 'report_file') 
            AND c.user_id = @user_id 
            ORDER BY c.created_at DESC
        """
        params = [{"name": "@user_id", "value": user_id}]
        
        items = list(container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        ))

        # 4. Rapikan Data
        history = []
        for item in items:
            history.append({
                "request_id": item.get("id"),
                "year": item.get("year"),
                "status": item.get("status"), # PROCESSING / COMPLETED / FAILED
                "file_url": item.get("file_url"), # None jika belum selesai
                "created_at": item.get("created_at"),
                "message": item.get("message")
            })

        return func.HttpResponse(
            json.dumps({"data": history}),
            status_code=200, 
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error GetHistory: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")