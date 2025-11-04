import json
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient
from datetime import datetime

def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = req.get_json()

        # Koneksi ke Azurite
        connection_string = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
        )

        table_service = TableServiceClient.from_connection_string(connection_string)
        transaction_table = table_service.get_table_client("transaction")

        # Simpan ke tabel
        entity = {
            "PartitionKey": "transaction",
            "RowKey": str(datetime.utcnow().timestamp()).replace('.', ''),
            "user_id": data["user_id"],
            "category_id": data.get("category_id", 0),
            "amount": float(data["amount"]),
            "description": data.get("description", ""),
            "transaction_date": datetime.utcnow().isoformat(),
            "source": data.get("source", "unknown"),
            "ai_confidence": "0.0"
        }

        transaction_table.create_entity(entity=entity)

        # Publish event ke Queue Transaction.Created
        queue_client = QueueClient.from_connection_string(
            conn_str=connection_string.replace("TableEndpoint", "QueueEndpoint").replace("10002", "10001"),
            queue_name="transaction-created"
        )
        queue_client.create_queue()
        queue_client.send_message(json.dumps(entity))

        return func.HttpResponse("Transaction created and event published.", status_code=200)

    except Exception as e:
        return func.HttpResponse(str(e), status_code=500)