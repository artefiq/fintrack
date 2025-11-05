import azure.functions as func
import json
import pyodbc
import os
from datetime import datetime
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential

def main(event: func.EventGridEvent):
    try:
        data = event.get_json()
        user_id = data.get("user_id")
        transactions = data.get("transactions", [])

        # Hitung total income & expense
        total_income = sum(t["amount"] for t in transactions if t["type"] == "income")
        total_expense = sum(t["amount"] for t in transactions if t["type"] == "expense")
        savings = total_income - total_expense

        # Koneksi ke database Azure SQL
        conn = pyodbc.connect(
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={os.getenv('DB_SERVER')};"
            f"DATABASE={os.getenv('DB_NAME')};"
            f"UID={os.getenv('DB_USER')};"
            f"PWD={os.getenv('DB_PASSWORD')};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )
        cursor = conn.cursor()

        # Simpan laporan baru
        cursor.execute("""
            INSERT INTO REPORT (user_id, report_type, total_income, total_expense, savings, generated_at, storage_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, "daily", total_income, total_expense, savings, datetime.utcnow(), None))
        conn.commit()
        cursor.close()
        conn.close()

        # Publish event Report.Updated
        topic_endpoint = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
        topic_key = os.getenv("EVENTGRID_ACCESS_KEY")
        event_grid_client = EventGridPublisherClient(topic_endpoint, AzureKeyCredential(topic_key))

        report_event = {
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

        event_grid_client.send([report_event])

        return func.HttpResponse("Report generated successfully", status_code=200)

    except Exception as e:
        return func.HttpResponse(str(e), status_code=500)
