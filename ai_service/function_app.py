import azure.functions as func
import logging
import json
import ai_core  # Mengimpor modul ai_core.py yang ada di folder yang sama

app = func.FunctionApp()

@app.route(route="ai/language", methods=["POST"])
def LanguageFunction(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('LanguageFunction (API Host) received HTTP request with AI Instructions.')

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON format. Please send valid request body."}), 
            mimetype="application/json",
            status_code=400
        )

    # Validasi Input (Membutuhkan 'text' DAN 'instructions')
    if not req_body or 'text' not in req_body or 'instructions' not in req_body:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'text' or 'instructions' field in the request body."}),
            mimetype="application/json",
            status_code=400
        )
    
    transaction_text = req_body.get('text')
    ai_instructions = req_body.get('instructions')

    try:
        # Panggil fungsi modular AI CORE
        ai_result = ai_core.process_ai_request(transaction_text, ai_instructions)
        
        # Merespons dengan hasil AI
        return func.HttpResponse(
            json.dumps(ai_result),
            mimetype="application/json",
            status_code=200
        )
        
    except (ConnectionError, RuntimeError, PermissionError) as e:
        # Menangani kesalahan konfigurasi atau API (misal: API Key salah)
        logging.error(f"AI Service configuration/API error: {e}")
        return func.HttpResponse(
            json.dumps({"error": f"AI Service Unavailable: {str(e)}"}),
            mimetype="application/json",
            status_code=503 
        )
    except Exception as e:
        # Menangani kesalahan umum lainnya
        logging.error(f"Unhandled error during processing: {e}")
        return func.HttpResponse(
             json.dumps({"error": f"Internal Server Error: {str(e)}"}),
             mimetype="application/json",
             status_code=500
        )