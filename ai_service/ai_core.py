import os
import logging
import json
from google import genai
from google.genai.errors import APIError

logging.getLogger().setLevel(logging.INFO)

# --- Initialisasi Klien Gemini ---
# Klien ini diinisialisasi sekali saat Azure Functions Host dimulai
try:
    API_KEY = os.environ["GEMINI_API_KEY"]
    
    # Inisialisasi klien Gemini menggunakan API Key dari local.settings.json
    gemini_client = genai.Client(api_key=API_KEY)
    AI_CLIENT_INITIALIZED = True
except KeyError as e:
    logging.error(f"Missing environment variable: {e}. Check local.settings.json for GEMINI_API_KEY.")
    AI_CLIENT_INITIALIZED = False
    gemini_client = None
except Exception as e:
    logging.error(f"General error during Gemini client initialization: {e}")
    AI_CLIENT_INITIALIZED = False
    gemini_client = None

def process_ai_request(text_input: str, ai_instruction: dict) -> dict:
    """
    Fungsi modular yang memproses permintaan AI (LLM) berdasarkan instruksi yang diberikan.
    
    Args:
        text_input (str): Teks transaksi.
        ai_instruction (dict): Instruksi AI (system_prompt, model_name) dari Category Service.
        
    Returns:
        dict: Hasil klasifikasi (category, ai_confidence).
    """
    
    if not AI_CLIENT_INITIALIZED:
        raise ConnectionError("Gemini Client not initialized. API Key is missing or invalid.")

    # Ambil instruksi dan prompt dari Category Service
    system_prompt = ai_instruction.get("system_prompt", "Anda adalah asisten kategorisasi keuangan yang profesional.")
    model_name = ai_instruction.get("model_name", "gemini-2.5-flash") 
    
    # 1. Tentukan Prompt ke LLM
    prompt_text = (
        f"Tugas: {system_prompt}\n\n"
        f"Kategorikan transaksi ini: '{text_input}'. "
        f"Kembalikan respons HANYA dalam format JSON dengan kunci 'category' (string) dan 'confidence' (float)."
    )
    
    try:
        # 2. Panggilan ke Gemini API
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=[prompt_text],
            # Konfigurasi untuk memastikan output adalah JSON yang mudah di-parse
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        # 3. Parsing Hasil
        raw_json_output = response.text
        ai_result = json.loads(raw_json_output)
        
        # 4. Kembalikan data yang siap digunakan
        return {
            "category": ai_result.get("category", "Uncategorized"),
            "ai_service_used": "gemini_llm",
            # LLM tidak selalu memberikan confidence score, jadi kita gunakan nilai dari output LLM
            "ai_confidence": float(ai_result.get("confidence", 0.99)) 
        }

    except APIError as e:
        logging.error(f"Gemini API Error (Status: {e.status_code}): {e}")
        # Kembalikan kategori error jika panggilan API gagal (misal: Quota atau Key salah)
        raise RuntimeError(f"Gemini API call failed: {e.status_code}")
    except json.JSONDecodeError:
        logging.error(f"Failed to parse JSON output from Gemini: {response.text}")
        # Kembalikan kategori error jika LLM tidak mengembalikan JSON yang benar
        return {
            "category": "Error_JSON_Parse",
            "ai_service_used": "gemini_llm",
            "ai_confidence": 0.0
        }
    except Exception as e:
        logging.error(f"General AI processing error: {e}")
        raise Exception(f"General AI processing error: {e}")