FROM mcr.microsoft.com/azure-functions/python:4-python3.11

RUN apt-get update && apt-get install -y build-essential python3-dev

COPY requirements.txt /
RUN pip install --upgrade pip && pip install -r /requirements.txt

COPY . /home/site/wwwroot
