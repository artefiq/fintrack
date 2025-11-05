import azure.functions as func
import logging
import json
import os
import uuid
from datetime import datetime
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
from azure.data.tables import TableServiceClient

app = func.FunctionApp()

# Cek apakah sedang dalam mode demo (untuk skip network call)
IS_LOCAL_DEMO = os.getenv("IS_LOCAL_DEMO", "false").lower() == "true"

# Koneksi string Azurite (baca dari local.settings.json)
AZURITE_TABLE_CONN_STR = os.getenv("AZURITE_TABLE_CONN_STR")

# -----------------------------------------------------------------
# FUNGSI 1: GenerateReportFunction (EventGridTrigger)
# Dipicu oleh: Event "Transaction.Categorized"
# -----------------------------------------------------------------
@app.event_grid_trigger(arg_name="event")
def GenerateReportFunction(event: func.EventGridEvent):
    logging.info(f"GenerateReportFunction dipicu oleh event: {event.event_type}")
    try:
        data = event.get_json()
        user_id = data.get("user_id")
        transactions = data.get("transactions", [])

        # Hitung total income & expense
        total_income = sum(t["amount"] for t in transactions if t["type"] == "income")
        total_expense = sum(t["amount"] for t in transactions if t["type"] == "expense")
        savings = total_income - total_expense

        # --- LOGIKA DATABASE DIUBAH KE AZURITE TABLE STORAGE ---
        service = TableServiceClient.from_connection_string(conn_str=AZURITE_TABLE_CONN_STR)
        report_table = service.get_table_client(table_name="report")

        # Buat entitas laporan baru
        new_report = {
            "PartitionKey": "report",
            "RowKey": str(uuid.uuid4()), # Buat RowKey unik baru
            "user_id": user_id,
            "report_type": "daily",
            "total_income": total_income,
            "total_expense": total_expense,
            "savings": savings,
            "generated_at": datetime.utcnow().isoformat(),
            "storage_path": None
        }
        
        logging.info(f"Menyimpan laporan harian untuk user {user_id} ke Azurite Table Storage")
        report_table.create_entity(entity=new_report)
        # --- PERUBAHAN LOGIKA DATABASE SELESAI ---

        # Publish event Report.Updated
        report_event_data = {
            "id": event.id,
            "subject": f"Report/Updated/{user_id}",
            "data": {
                "user_id": user_id,
                "total_income": total_income,
                "total_expense": total_expense,
                "savings": savings
            },
            "eventType": "Report.Updated",
            "eventTime": datetime.utcnow().isoformat(),
            "dataVersion": "1.0"
        }

        # --- Cek mode demo ---
        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'Report.Updated' tidak dikirim. Payload: {report_event_data['data']}")
        else:
            topic_endpoint = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
            topic_key = os.getenv("EVENTGRID_ACCESS_KEY")
            event_grid_client = EventGridPublisherClient(topic_endpoint, AzureKeyCredential(topic_key))
            event_grid_client.send([report_event_data])
            logging.info("Event 'Report.Updated' berhasil dikirim.")

    except Exception as e:
        logging.error(f"Error di GenerateReportFunction: {e}")
        raise e

# -----------------------------------------------------------------
# FUNGSI 2: MonthlyReportSchedulerFunction (TimerTrigger)
# -----------------------------------------------------------------
@app.schedule(schedule="0 0 0 1 * *", arg_name="mytimer", run_on_startup=False) 
def MonthlyReportSchedulerFunction(mytimer: func.TimerRequest) -> None:
    utc_timestamp = datetime.utcnow().isoformat()
    logging.info(f"MonthlyReportSchedulerFunction berjalan pada: {utc_timestamp}")

    try:
        event_data = {
            "id": "month-ended-" + utc_timestamp,
            "subject": "Month/Ended",
            "data": {"month": datetime.utcnow().strftime("%Y-%m")},
            "eventType": "Month.Ended",
            "eventTime": utc_timestamp,
            "dataVersion": "1.0"
        }

        # --- Cek mode demo ---
        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'Month.Ended' tidak dikirim. Payload: {event_data['data']}")
        else:
            topic_endpoint = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
            topic_key = os.getenv("EVENTGRID_ACCESS_KEY")
            event_grid_client = EventGridPublisherClient(
                topic_endpoint, AzureKeyCredential(topic_key)
            )
            event_grid_client.send([event_data])
        
        logging.info(f"Event 'Month.Ended' berhasil diproses (mode: {'demo' if IS_LOCAL_DEMO else 'publish'}).")

    except Exception as e:
        logging.error(f"Error in MonthlyReportSchedulerFunction: {e}")
        raise e

# -----------------------------------------------------------------
# FUNGSI 3: OnMonthEndedFunction (EventGridTrigger)
# -----------------------------------------------------------------
@app.event_grid_trigger(arg_name="event")
def OnMonthEndedFunction(event: func.EventGridEvent):
    logging.info(f"OnMonthEndedFunction dipicu oleh event: {event.event_type}")
    try:
        month = event.get_json().get("month")
        logging.info(f"Generating monthly report for {month}")

        # --- LOGIKA DATABASE DIUBAH KE AZURITE TABLE STORAGE ---
        service = TableServiceClient.from_connection_string(conn_str=AZURITE_TABLE_CONN_STR)
        transaction_table = service.get_table_client(table_name="transaction")
        report_table = service.get_table_client(table_name="report")

        # 1. Ambil semua user (dari tabel 'user' atau 'transaction')
        # Menggunakan 'transaction' table
        entities = transaction_table.query_entities(query_filter="", select=["user_id"])
        user_ids = set(e["user_id"] for e in entities) # Gunakan 'set' untuk dapat user unik

        for user_id in user_ids:
            logging.info(f"Memproses laporan bulanan untuk user {user_id}...")
            
            # 2. Agregasi transaksi bulan ini (harus manual di Python, Table Storage tidak bisa SUM)
            total_income = 0.0
            total_expense = 0.0
            
            # Buat query untuk filter transaksi user & bulan
            # Table Storage tidak punya fungsi 'FORMAT', jadi harus mengambil semua dan filter di Python
            # Ga efisien, tapi ok untuk demo lokal
            user_transactions = transaction_table.query_entities(query_filter=f"user_id eq {user_id}")

            for t in user_transactions:
                # Filter bulan di Python
                trans_date = datetime.fromisoformat(t["transaction_date"])
                if trans_date.strftime("%Y-%m") == month:
                    if t["category_id"] == 2: # Asumsi Gaji (Income)
                        total_income += t["amount"]
                    else: # Asumsi lain (Expense)
                        total_expense += t["amount"]

            savings = total_income - total_expense

            # 3. Simpan ke REPORT
            new_report = {
                "PartitionKey": "report",
                "RowKey": str(uuid.uuid4()),
                "user_id": user_id,
                "report_type": "monthly",
                "total_income": total_income,
                "total_expense": total_expense,
                "savings": savings,
                "generated_at": datetime.utcnow().isoformat(),
                "storage_path": None
            }
            report_table.create_entity(entity=new_report)
            logging.info(f"Laporan bulanan untuk user {user_id} berhasil disimpan.")
        
        # --- PERUBAHAN LOGIKA DATABASE SELESAI ---

        # Publish event Report.Generated
        report_event_data = {
            "id": f"report-generated-{month}",
            "subject": f"Report/Generated/{month}",
            "data": {"month": month},
            "eventType": "Report.Generated",
            "eventTime": datetime.utcnow().isoformat(),
            "dataVersion": "1.0"
        }

        # --- Cek mode demo ---
        if IS_LOCAL_DEMO:
            logging.warning(f"MODE DEMO: Event 'Report.Generated' tidak dikirim. Payload: {report_event_data['data']}")
        else:
            topic_endpoint = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
            topic_key = os.getenv("EVENTGRID_ACCESS_KEY")
            event_grid_client = EventGridPublisherClient(topic_endpoint, AzureKeyCredential(topic_key))
            event_grid_client.send([report_event_data])
            logging.info("Event 'Report.Generated' berhasil dikirim.")

        logging.info(f"✅ Laporan bulanan dan event 'Report.Generated' berhasil diproses untuk {month}")

    except Exception as e:
        logging.error(f"Error di OnMonthEndedFunction: {e}")
        raise e

