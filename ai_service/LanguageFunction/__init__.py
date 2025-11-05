import logging
import json
import azure.functions as func
from .. import ai_core # Mengimpor fungsi modular dari ai_core.py

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('LanguageFunction (API Host) received HTTP request with AI Instructions.')

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON format. Please send valid request body.", status_code=400)

    # Validasi Input (Membutuhkan 'text' DAN 'instructions')
    if not req_body or 'text' not in req_body or 'instructions' not in req_body:
        return func.HttpResponse(
            "Missing 'text' or 'instructions' field in the request body.",
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
        # Menangani kesalahan konfigurasi atau API
        logging.error(f"AI Service configuration/API error: {e}")
        return func.HttpResponse(
            f"AI Service Unavailable: {str(e)}",
            status_code=503 
        )
    except Exception as e:
        # Menangani kesalahan umum lainnya
        logging.error(f"Unhandled error during processing: {e}")
        return func.HttpResponse(
             f"Internal Server Error: {str(e)}",
             status_code=500
        )