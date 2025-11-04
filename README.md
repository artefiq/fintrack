# 💰 FinTrack — Cloud-based Financial Management Platform

FinTrack adalah platform **keuangan pribadi berbasis cloud** yang membantu pengguna mencatat, mengelola, dan menganalisis keuangan dengan mudah.  
Aplikasi ini dibangun menggunakan **Azure Functions** dan arsitektur **microservices**, serta memanfaatkan layanan **AI, database, dan storage** dari Microsoft Azure.

---

## 🚀 Tujuan Proyek
- Memberikan solusi **"super app" finansial sederhana** berbasis cloud.
- Mengimplementasikan **serverless architecture** dengan Azure Functions.
- Mengadopsi pendekatan **event-driven** agar tiap service berjalan independen.
- Memanfaatkan **AI dan Cognitive Services** untuk klasifikasi transaksi & insight keuangan.

---

## 🏗️ Arsitektur Sistem

Setiap komponen dijalankan sebagai **Azure Function App** (serverless microservice).  
Komunikasi antar-service dilakukan melalui **Azure Service Bus (event-driven)**.

```
+---------------------+
|     API Gateway     |
|  (Azure Function)   |
+----------+----------+
           |
           ↓
 ┌───────────────────────┐
 |  UserService          |
 |  - Register/Login     |
 |  - Profile Management |
 └───────────────────────┘
           ↓
 ┌───────────────────────┐
 |  TransactionService   |
 |  - Create Transaction |
 |  - Emit Event         |
 └───────────────────────┘
           ↓
 ┌────────────────────────┐
 |  CategoryService (AI)  |
 |  - Classify Category   |
 |  - Store Result        |
 └────────────────────────┘
           ↓
 ┌────────────────────────┐
 |  ReportService         |
 |  - Generate Dashboard  |
 |  - Trend Analysis      |
 └────────────────────────┘
           ↓
 ┌────────────────────────┐
 |  AIService (Chatbot)   |
 |  - Insight Assistant   |
 └────────────────────────┘
```

📡 **Event-driven flow example:**
1. Transaction dibuat → event dikirim ke Service Bus.  
2. CategoryService mendengarkan event itu → klasifikasi transaksi.  
3. ReportService update laporan pengguna.  
4. AIService siap memberikan insight lewat chatbot.

---

## ☁️ Azure Services yang Digunakan

+--------------------------------------------------------------------------------------+
| Komponen     | Layanan Azure                 | Fungsi                                |
|--------------|-------------------------------|---------------------------------------|
| Backend      | Azure Functions               | Serverless API tiap microservice      |
| Database     | Azure SQL Database            | Penyimpanan data transaksi & user     |
| Eventing     | Azure Service Bus             | Pengiriman event antar-service        |
| File Storage | Azure Blob Storage            | Upload & simpan struk transaksi       |
| AI/ML        | Azure Cognitive Services      | Kategorisasi otomatis transaksi       |
| Security     | Azure Key Vault               | Menyimpan connection string & secrets |
| Container    | Azure Container Apps / Docker | Menjalankan service di container      |
| Monitoring   | Azure Application Insights    | Logging dan pemantauan performa       |
+--------------------------------------------------------------------------------------+

---

## 📁 Struktur Folder

```
fintrack/
│
├─ docker-compose.yml
├─ README.md
│
├─ user_service/
│  ├─ Dockerfile
│  ├─ host.json
│  ├─ local.settings.json
│  └─ functions/
│     ├─ create_user/
│     ├─ get_user/
│     └─ delete_user/
│
├─ transaction_service/
│  ├─ Dockerfile
│  ├─ host.json
│  ├─ functions/
│     ├─ create_transaction/
│     └─ notify_transaction/
│
├─ category_service/
│  ├─ Dockerfile
│  └─ functions/
│     └─ categorize_transaction/
│
├─ report_service/
│  ├─ Dockerfile
│  └─ functions/
│     └─ generate_report/
│
├─ ai_service/
│  ├─ Dockerfile
│  └─ functions/
│     └─ chatbot/
│
└─ api_gateway/
   ├─ Dockerfile
   └─ functions/
      └─ proxy_request/
```

---

## ⚙️ Instalasi & Setup

### 1️⃣ pre-requisite
Pastikan sudah menginstal:
- 🐳 [Docker Desktop](https://www.docker.com/)
- 🪟 **Windows:** pastikan **WSL 2** sudah aktif  
  ```bash
  wsl --install
  wsl --set-default-version 2
  ```
- ☁️ [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
- 🔑 Azure CLI (untuk login & deploy)

---

### 2️⃣ Menjalankan di Lokal

Clone repositori:
```bash
git clone https://github.com/artefiq/fintrack.git
cd fintrack
```

Jalankan docker-compose:
```bash
docker-compose build
docker-compose up
```

---

## 🧠 Pembagian Peran Tim

+---------------------------------------------------------------------------------------------------------------------+
| Person   | Fokus Utama           | Azure Functions yang Dikerjakan         | Layanan Azure Utama                    |
|----------|-----------------------|-----------------------------------------|----------------------------------------|
| Person 1 | AI / ML Engineer      | `category_service`, `ai_service`        | Cognitive Services, Blob Storage       |
| Person 2 | Data Engineer         | `transaction_service`, `report_service` | SQL Database, Service Bus              |
| Person 3 | Backend / Integration | `user_service`, `api_gateway`           | Azure Functions Core, Key Vault, CI/CD |
+---------------------------------------------------------------------------------------------------------------------+

---

## 🔐 Environment Variables

Gunakan **Azure Key Vault** untuk menyimpan nilai rahasia seperti:
```
SQL_CONN_STRING
SERVICE_BUS_CONN
BLOB_CONN_STRING
COGNITIVE_API_KEY
```

Selama lokal development, tambahkan di `local.settings.json`:
```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "SQL_CONN_STRING": "Server=...;Database=...;",
    "SERVICE_BUS_CONN": "...",
    "COGNITIVE_API_KEY": "..."
  }
}
```

---

## 🧩 Event-driven Integration

- `TransactionService` mem-publish event ke **Service Bus Queue** (`transaction-created`).
- `CategoryService` subscribe ke queue itu dan update kategori transaksi.
- `ReportService` listen event `category-updated` untuk update laporan.
- `AIService` bisa consume event untuk memberi insight tambahan.

---

## 🚀 Deployment ke Azure

1. Login ke Azure:
   ```bash
   az login
   ```
2. Deploy masing-masing Function App:
   ```bash
   func azure functionapp publish fintrack-user-service
   func azure functionapp publish fintrack-transaction-service
   ```
3. Pastikan semua `Connection String` dan `App Settings` tersimpan di Azure Portal.

---

## 📊 Monitoring & Logging

- Semua Function App dikonfigurasi ke **Application Insights**.
- Gunakan **Log Stream** di Azure Portal untuk debugging real-time.

---

## 🧾 Lisensi
MIT License © 2025 FinTrack Team