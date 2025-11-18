# 1. Gunakan image dasar resmi dari Microsoft untuk Functions V4 + Python 3.11
# Kita tetap konsisten menggunakan Python 3.11 agar stabil dengan service lain.
FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# 2. Set environment variables standar Azure Functions
ENV AzureWebJobsScriptRoot=/home/site/wwwroot
ENV FUNCTIONS_WORKER_RUNTIME=python

# 3. Instal build tools dasar
# Langkah ini 'best practice' untuk memastikan jika ada library Python
# yang butuh kompilasi C (seperti grpcio di masa depan), build tidak gagal.
RUN apt-get update && \
    apt-get install -y build-essential && \
    rm -rf /var/lib/apt/lists/*

# 4. Salin requirements.txt terlebih dahulu (untuk caching layer Docker)
COPY requirements.txt /

# 5. Instal dependencies
RUN pip install --upgrade pip
RUN pip install -r /requirements.txt

# 6. Salin sisa kode aplikasi Anda (function_app.py, ai_core.py, dll)
COPY . /home/site/wwwroot