# 1. Gunakan image dasar resmi dari Microsoft untuk Functions V4 + Python 3.11
FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# 2. Set environment variables yang dibutuhkan oleh Functions Host
ENV AzureWebJobsScriptRoot=/home/site/wwwroot
ENV FUNCTIONS_WORKER_RUNTIME=python

# 3. Salin file requirements.txt terlebih dahulu (untuk optimasi cache Docker)
# Pastikan requirements.txt Anda berisi:
# azure-functions
# azure-eventgrid
# azure-data-tables
COPY requirements.txt /

# 4. Instal dependencies Python
# Kita TIDAK PERLU 'apt-get install' driver ODBC karena kita pakai 'azure-data-tables'
RUN pip install --upgrade pip \
    && pip install -r /requirements.txt

# 5. Salin sisa kode aplikasi Anda (function_app.py, host.json, dll)
COPY . /home/site/wwwroot

