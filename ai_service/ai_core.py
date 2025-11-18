import os
import logging
import json
from google import genai
from google.genai.errors import APIError

# Setup Logging
logger = logging.getLogger(__name__)

# --- Initialisasi Klien Gemini ---
# Variabel global untuk menyimpan status klien
gemini_client = None
AI_CLIENT_INITIALIZED = False

def _initialize_client():
    """
    Fungsi internal untuk inisialisasi lazy loading.
    Hanya mencoba connect saat request pertama masuk, atau jika belum connect.
    """
    global gemini_client, AI_CLIENT_INITIALIZED
    
    if AI_CLIENT_INITIALIZED:
        return

    try:
        API_KEY = os.environ.get("GEMINI_API_KEY")
        if not API_KEY:
            raise KeyError("GEMINI_API_KEY not found in environment variables.")
            
        # Inisialisasi klien Gemini
        gemini_client = genai.Client(api_key=API_KEY)
        AI_CLIENT_INITIALIZED = True
        logger.info("Gemini Client successfully initialized.")
        
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
        AI_CLIENT_INITIALIZED = False
        gemini_client = None

def process_ai_request(text_input: str, ai_instruction: dict) -> dict:
    """
    Fungsi modular yang memproses permintaan AI (LLM).
    """
    # Coba inisialisasi jika belum
    _initialize_client()

    if not AI_CLIENT_INITIALIZED:
        raise ConnectionError("Gemini Client not initialized. Check GEMINI_API_KEY configuration.")

    # Ambil instruksi dan prompt
    system_prompt = ai_instruction.get("system_prompt", "Anda adalah asisten kategorisasi keuangan profesional.")
    model_name = ai_instruction.get("model_name", "gemini-2.0-flash") # Gunakan model terbaru/stabil
    
    # 1. Tentukan Prompt ke LLM
    # Tips: Berikan contoh format JSON di prompt agar akurasi lebih tinggi
    prompt_text = (
        f"Role: {system_prompt}\n"
        f"Input Transaksi: '{text_input}'\n\n"
        f"Instruksi: Analisis input di atas. "
        f"Output WAJIB JSON valid tanpa markdown (```json). "
        f"Format JSON: {{ \"category\": \"string\", \"confidence\": float (0.0-1.0) }}"
    )
    
    try:
        # 2. Panggilan ke Gemini API
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=[prompt_text],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        # 3. Parsing Hasil
        raw_text = response.text
        # Bersihkan markdown jika LLM bandel (opsional tapi aman)
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "").replace("```", "")
            
        ai_result = json.loads(raw_text)
        
        # 4. Kembalikan data yang siap digunakan
        return {
            "category": ai_result.get("category", "Uncategorized"),
            "ai_service_used": "gemini_llm",
            "ai_confidence": float(ai_result.get("confidence", 0.99))
        }

    except APIError as e:
        logger.error(f"Gemini API Error: {e}")
        raise RuntimeError(f"Gemini API call failed: {e}")
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from Gemini: {response.text}")
        return {
            "category": "Error_JSON_Parse",
            "ai_service_used": "gemini_llm",
            "ai_confidence": 0.0
        }
    except Exception as e:
        logger.error(f"General AI processing error: {e}")
        raise Exception(f"General AI processing error: {e}")