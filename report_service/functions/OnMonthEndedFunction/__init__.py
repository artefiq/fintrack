import azure.functions as func
import pyodbc
from datetime import datetime
import os
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
import json

def main(event: func.EventGridEvent):
    try:
        month = event.get_json().get("month")
        print(f"Generating monthly report for {month}")

        conn = pyodbc.connect(
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={os.getenv('DB_SERVER')};"
            f"DATABASE={os.getenv('DB_NAME')};"
            f"UID={os.getenv('DB_USER')};"
            f"PWD={os.getenv('DB_PASSWORD')};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )
        cursor = conn.cursor()

        # Ambil semua user
        cursor.execute("SELECT DISTINCT user_id FROM TRANSACTION")
        users = [row[0] for row in cursor.fetchall()]

        for user_id in users:
            # Agregasi transaksi bulan ini
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN type='income' THEN amount ELSE 0 END),
                    SUM(CASE WHEN type='expense' THEN amount ELSE 0 END)
                FROM TRANSACTION
                WHERE user_id = ? AND FORMAT(date, 'yyyy-MM') = ?
            """, (user_id, month))
            row = cursor.fetchone()
            total_income = row[0] or 0
            total_expense = row[1] or 0
            savings = total_income - total_expense

            # Simpan ke REPORT
            cursor.execute("""
                INSERT INTO REPORT (user_id, report_type, total_income, total_expense, savings, generated_at, storage_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, "monthly", total_income, total_expense, savings, datetime.utcnow(), None))
            conn.commit()

        cursor.close()
        conn.close()

        # Publish event Report.Generated
        topic_endpoint = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
        topic_key = os.getenv("EVENTGRID_ACCESS_KEY")

        event_grid_client = EventGridPublisherClient(topic_endpoint, AzureKeyCredential(topic_key))
        report_event = {
            "id": f"report-generated-{month}",
            "subject": f"Report/Generated/{month}",
            "data": {"month": month},
            "eventType": "Report.Generated",
            "eventTime": datetime.utcnow().isoformat(),
            "dataVersion": "1.0"
        }
        event_grid_client.send([report_event])

        print(f"✅ Monthly reports generated and Report.Generated event published for {month}")

    except Exception as e:
        print(f"❌ Error generating report: {e}")
