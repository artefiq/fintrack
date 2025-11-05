import azure.functions as func
import json
import os
import requests

def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        name = body.get("name")
        email = body.get("email")

        if not name or not email:
            return func.HttpResponse(
                json.dumps({"error": "Name and email are required"}),
                mimetype="application/json",
                status_code=400
            )

        # Simpan user ke database (sementara log saja)
        print(f"[INFO] Register user: {name} ({email})")

        # Siapkan event untuk Event Grid
        event = [
            {
                "id": email,
                "eventType": "User.Created",
                "subject": f"/users/{email}",
                "data": {"name": name, "email": email},
                "dataVersion": "1.0"
            }
        ]

        # Kirim ke Event Grid
        event_grid_url = os.getenv("EVENT_GRID_TOPIC_ENDPOINT")
        event_grid_key = os.getenv("EVENT_GRID_KEY")

        headers = {
            "aeg-sas-key": event_grid_key,
            "Content-Type": "application/json"
        }

        response = requests.post(event_grid_url, headers=headers, data=json.dumps(event))

        if response.status_code >= 200 and response.status_code < 300:
            print("[SUCCESS] Event published to Event Grid")
        else:
            print(f"[ERROR] Failed to publish event: {response.status_code} - {response.text}")

        return func.HttpResponse(
            json.dumps({"status": "User registered", "event_status": response.status_code}),
            mimetype="application/json"
        )

    except Exception as e:
        print(f"[EXCEPTION] {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )
