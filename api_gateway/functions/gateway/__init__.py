import azure.functions as func
import requests
import os
import json

def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        path = req.route_params.get('path') or ''
        method = req.method

        # Routing sederhana berdasarkan prefix path
        if path.startswith("user"):
            target = os.getenv("USER_SERVICE_URL")
        elif path.startswith("transaction"):
            target = os.getenv("TRANSACTION_SERVICE_URL")
        elif path.startswith("category"):
            target = os.getenv("CATEGORY_SERVICE_URL")
        elif path.startswith("report"):
            target = os.getenv("REPORT_SERVICE_URL")
        elif path.startswith("ai"):
            target = os.getenv("AI_SERVICE_URL")
        else:
            return func.HttpResponse(
                json.dumps({"error": "Unknown path"}),
                mimetype="application/json",
                status_code=404
            )

        # Kirim request ke service tujuan
        resp = requests.request(
            method=method,
            url=f"{target}/{path}",
            headers=req.headers,
            data=req.get_body()
        )

        return func.HttpResponse(
            resp.text,
            mimetype="application/json",
            status_code=resp.status_code
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )
