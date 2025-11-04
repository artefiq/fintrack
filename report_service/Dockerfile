FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# Set working directory
WORKDIR /home/site/wwwroot

# Copy requirements and install dependencies
COPY requirements.txt /
RUN apt-get update && \
    apt-get install -y unixodbc-dev gcc g++ && \
    pip install --upgrade pip && \
    pip install -r /requirements.txt

# Copy function app code
COPY . /home/site/wwwroot
