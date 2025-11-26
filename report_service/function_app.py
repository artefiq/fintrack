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
        year = event_data.get("year")
        req_id = event_data.get("request_id")

        logging.info(f"🚀 Job Berat: Report {year} User {user_id}")

        container = get_container()
        
        # Query Transaksi dari Cosmos DB
        query = "SELECT * FROM c WHERE c.type = 'transaction' AND c.user_id = @user_id"
        params = [{"name": "@user_id", "value": str(user_id)}]
        
        user_trans = list(container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        ))
        
        data_list = []
        for t in user_trans:
            try:
                trans_date = datetime.fromisoformat(t["transaction_date"])
                if str(trans_date.year) == str(year):
                    data_list.append({
                        "Date": t["transaction_date"],
                        "Description": t["description"],
                        "Amount": t["amount"],
                        "Category": t.get("ai_category_name", "Uncategorized")
                    })
            except:
                continue

        if not data_list:
            logging.warning("Data kosong.")
            return

        # Proses Pandas
        df = pd.DataFrame(data_list)
        pivot_summary = df.pivot_table(index="Category", values="Amount", aggfunc="sum")
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Transactions', index=False)
            pivot_summary.to_excel(writer, sheet_name='Summary')
        
        output.seek(0)
        file_content = output.getvalue()

        # Upload Blob (Tetap pakai Storage Account biasa)
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

        # Publish Completion Event
        completion_event = {
            "id": str(uuid.uuid4()),
            "subject": f"Report/Generation/Completed/{user_id}",
            "data": {
                "user_id": user_id,
                "status": "COMPLETED",
                "download_url": blob_url
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