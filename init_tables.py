from azure.data.tables import TableServiceClient
from datetime import datetime

# Konfigurasi koneksi ke Azurite lokal 
connection_string = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)

# Inisialisasi koneksi 
service = TableServiceClient.from_connection_string(conn_str=connection_string)

# Daftar tabel 
tables = ["user", "category", "transaction", "budget", "report"]

for table_name in tables:
    try:
        service.create_table_if_not_exists(table_name=table_name)
        print(f"Tabel '{table_name}' berhasil dibuat atau sudah ada.")
    except Exception as e:
        print(f"Gagal membuat tabel {table_name}: {e}")

# Seed data contoh 

# USER
user_table = service.get_table_client("user")
user_table.create_entity({
    "PartitionKey": "user",
    "RowKey": "1",
    "name": "John Doe",
    "email": "john@example.com",
    "created_at": datetime.utcnow().isoformat()
})
print("User ditambahkan.")

# CATEGORY
category_table = service.get_table_client("category")
category_table.create_entity({
    "PartitionKey": "category",
    "RowKey": "1",
    "category_name": "Makanan & Minuman",
    "category_type": "Expense",
    "user_id": 1
})
category_table.create_entity({
    "PartitionKey": "category",
    "RowKey": "2",
    "category_name": "Gaji",
    "category_type": "Income",
    "user_id": 1
})
print("Category ditambahkan.")

# TRANSACTION
transaction_table = service.get_table_client("transaction")
transaction_table.create_entity({
    "PartitionKey": "transaction",
    "RowKey": "1",
    "user_id": 1,
    "category_id": 1,
    "amount": 50000.0,
    "description": "Beli kopi",
    "transaction_date": datetime.utcnow().isoformat(),
    "source": "cash",
    "ai_confidence": "0.92"
})
transaction_table.create_entity({
    "PartitionKey": "transaction",
    "RowKey": "2",
    "user_id": 1,
    "category_id": 2,
    "amount": 3000000.0,
    "description": "Gaji Bulanan",
    "transaction_date": datetime.utcnow().isoformat(),
    "source": "transfer",
    "ai_confidence": "0.98"
})
print("Transaction ditambahkan.")

# BUDGET
budget_table = service.get_table_client("budget")
budget_table.create_entity({
    "PartitionKey": "budget",
    "RowKey": "1",
    "user_id": 1,
    "category_id": 1,
    "monthly_limit": 1000000.0,
    "period_start": datetime(2025, 11, 1).isoformat(),
    "period_end": datetime(2025, 11, 30).isoformat()
})
print("Budget ditambahkan.")

# REPORT
report_table = service.get_table_client("report")
report_table.create_entity({
    "PartitionKey": "report",
    "RowKey": "1",
    "user_id": 1,
    "report_type": "Monthly",
    "total_income": 3000000.0,
    "total_expense": 50000.0,
    "savings": 2950000.0,
    "generated_at": datetime.utcnow().isoformat(),
    "storage_path": "/reports/2025-11/report1.pdf"
})
print("Report ditambahkan.")

print("all done")