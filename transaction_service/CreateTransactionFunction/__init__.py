import json
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient
from datetime import datetime

def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = req.get_json()

        # Connection String (Azurite)
        table_conn_str = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
        )

        queue_conn_str = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
        )

        # Table Service
        table_service = TableServiceClient.from_connection_string(table_conn_str)
        table_client = table_service.get_table_client("transaction")

        try:
            table_service.create_table_if_not_exists("transaction")
        except Exception:
            pass  # tabel sudah ada

        # Entity
        entity = {
            "PartitionKey": "transaction",
            "RowKey": str(datetime.utcnow().timestamp()).replace('.', ''),
            "user_id": data["user_id"],
            "category_id": 0,                             # <-- DEFAULT: UNCATEGORIZED
            # "amount": float(data.get("amount", 0.0)),     # <-- DEFAULT: 0.0 (Jika amount tidak ada di request)
            "amount": 0.0,     
            "description": data["description"],           # <-- Input wajib dari user (e.g., "5k beli air")
            "transaction_date": datetime.utcnow().isoformat(),
            "source": data.get("source", "unknown"),
            "ai_confidence": "0.0",
            "input_type": data.get("input_type", "text")
        }

        table_client.create_entity(entity=entity)

        # Queue Service
        queue_client = QueueClient.from_connection_string(
            conn_str=queue_conn_str,
            queue_name="transaction-created"
        )

        try:
            queue_client.create_queue()
        except Exception:
            pass  # queue sudah ada

        queue_client.send_message(json.dumps(entity))

        return func.HttpResponse(
            "Transaction created and event published.",
            status_code=200
        )

    except Exception as e:
        return func.HttpResponse(f"❌ Error: {str(e)}", status_code=500)
