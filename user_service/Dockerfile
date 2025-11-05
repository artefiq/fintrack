FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# 2. Set variabel environment
ENV AzureWebJobsScriptRoot=/home/site/wwwroot
ENV FUNCTIONS_WORKER_RUNTIME=python

# 3. Instal 'build-essential'
RUN apt-get update && \
    apt-get install -y build-essential gcc g++

# 4. Salin file requirements.txt
COPY requirements.txt /

# 5. Instal dependencies dari requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /requirements.txt

# 6. Salin sisa kode aplikasi Anda (function_app.py, host.json, dll)
# ke direktori yang diharapkan oleh Azure Functions.
COPY . /home/site/wwwroot
