import os
import logging
import json
from google import genai
from google.genai.errors import APIError

# --- LIBRARY BARU UNTUK AZURE OCR ---
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential

# Setup Logging
logger = logging.getLogger(__name__)

# --- GLOBAL VARS ---
gemini_client = None
AI_CLIENT_INITIALIZED = False

# ==========================================
# BAGIAN 1: INISIALISASI KLIEN (GEMINI & AZURE)
# ==========================================

def _initialize_gemini():
    """Lazy load untuk Gemini Client"""
    global gemini_client, AI_CLIENT_INITIALIZED
    if AI_CLIENT_INITIALIZED: return

    try:
        API_KEY = os.environ.get("GEMINI_API_KEY")
        if not API_KEY: raise KeyError("GEMINI_API_KEY missing.")
        gemini_client = genai.Client(api_key=API_KEY)
        AI_CLIENT_INITIALIZED = True
        logger.info("Gemini Client initialized.")
    except Exception as e:
        logger.error(f"Gemini Init Failed: {e}")
        AI_CLIENT_INITIALIZED = False

def _get_azure_client():
    """Helper untuk membuat Azure Document Intelligence Client saat dibutuhkan"""
    endpoint = os.environ.get("AZURE_FORM_ENDPOINT")
    key = os.environ.get("AZURE_FORM_KEY")
    
    if not endpoint or not key:
        raise ValueError("AZURE_FORM_ENDPOINT or AZURE_FORM_KEY is missing.")
        
    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

def process_ai_request(text_input: str, ai_instruction: dict) -> dict:
    """
    Fungsi modular yang memproses permintaan AI (LLM).
    """
    # Coba inisialisasi jika belum
    _initialize_gemini()

    if not AI_CLIENT_INITIALIZED:
        raise ConnectionError("Gemini Client not initialized. Check GEMINI_API_KEY configuration.")

    # Ambil instruksi dan prompt
    system_prompt = ai_instruction.get("system_prompt", "Anda adalah asisten kategorisasi keuangan profesional.")
    model_name = ai_instruction.get("model_name", "gemini-2.5-flash") 

    # 1. Tentukan Prompt ke LLM
    # Tips: Berikan contoh format JSON di prompt agar akurasi lebih tinggi
    prompt_text = (
        f"Role: {system_prompt}\n"
        f"Input Transaksi: '{text_input}'\n\n"
        f"Instruksi: Analisis input di atas. "
        f"Output WAJIB JSON valid tanpa markdown (```json). "
        # f"Format JSON: {{ \"category\": \"string\", \"confidence\": float (0.0-1.0) }}"
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
        logger.info(f"AI Result: {ai_result}")
        
        # 4. Kembalikan data yang siap digunakan
        return {
            "category_name": ai_result.get("category_name", "Uncategorized"),
            "category_type": ai_result.get("category_type", "Uncategorized"),
            "amount": float(ai_result.get("amount", 0.0)),
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

def process_receipt_ocr(image_url: str, ai_instruction: dict) -> dict: 
    try:
        # 1. AZURE OCR
        azure_client = _get_azure_client()
        poller = azure_client.begin_analyze_document_from_url("prebuilt-receipt", image_url)
        result = poller.result()
        
        if not result.documents:
            raise Exception("No document detected by Azure")

        receipt = result.documents[0]
        
        # --- STRATEGI A: COBA AMBIL DARI FIELDS (Structured) ---
        merchant = receipt.fields.get("MerchantName")
        merchant_str = merchant.value if merchant else "Unknown"
        
        total_field = receipt.fields.get("Total")
        azure_amount = total_field.value if total_field else 0.0
        
        # Ambil items dari fields
        items_list = []
        if "Items" in receipt.fields:
            for item in receipt.fields.get("Items").value:
                d = item.value.get("Description")
                if d: items_list.append(d.value)
        items_str = ", ".join(items_list)

        # --- STRATEGI B: FALLBACK KE RAW CONTENT (Unstructured) ---
        # Ini adalah jaring pengaman jika fields gagal
        raw_full_text = result.content  # <--- INI KUNCINYA
        logger.info(f"Azure OCR Raw Text: {raw_full_text}")
        
        # Tentukan teks mana yang akan dikirim ke Gemini
        if azure_amount > 0 and merchant_str != "Unknown":
            # Jika Azure sukses, kirim ringkasan saja (hemat token)
            text_for_ai = f"Merchant: {merchant_str}. Items: {items_str}. Total: {azure_amount}"
            logger.info("Azure Fields Valid. Using structured data.")
        else:
            # JIKA AZURE FIELDS SALAH/KOSONG:
            # Kirim SELURUH teks mentah ke Gemini. Biarkan Gemini yang mencari totalnya.
            text_for_ai = f"""
            SYSTEM WARNING: Azure gagal untuk mengekstrak field terstruktur dengan sempurna.
            Ini adalah FULL RAW TEXT dari receipt:
            ---
            {raw_full_text}
            ---
            RULES PENTING UNTUK EKSTRAKSI AMOUNT:
            1. Struk adalah format indonesia.
            2. Simbol titik (.) adalah PEMISAH RIBUAN, BUKAN titik desimal.
            - Contoh: "66.900" artinya 66900.
            - Contoh: "1.500" artinya 1500.
            3. RETURN 'amount' SEBAGAI NUMBER ASLI (INTEGER/FLOAT) TANPA SEPARATOR ANEH.
            - WRONG: 66.900
            - WRONG: 66,900
            - CORRECT: 66900
            """
            logger.warning("Azure Fields Incomplete/Zero. Sending RAW CONTENT to Gemini.")

        # 2. GEMINI PROCESSING (Dengan Retry Logic 429)
        cat_result = {}
        cat_result = process_ai_request(text_for_ai, ai_instruction)
        logger.info(f"Gemini Categorization Result: {cat_result}")
        # max_retries = 3
        # for attempt in range(max_retries):
        #     try:
        #         # Pastikan prompt di process_ai_request meminta return JSON dengan field 'amount'
        #         break 
        #     except Exception as e:
        #         if "429" in str(e):
        #             time.sleep(2 ** attempt)
        #         else:
        #             break

        # 3. FINAL MERGE
        # Prioritas nilai Amount:
        # 1. Gemini (karena dia melihat raw text atau memperbaiki Azure)
        # 2. Azure (kalau Gemini gagal/error/tidak nemu)
        
        final_amount = cat_result.get("amount", 0.0)
        if final_amount == 0.0:
            final_amount = azure_amount

        # C. Gabungkan Hasil
        return {
            "description": raw_full_text, 
            "amount": final_amount, # Amount diambil dari Azure (lebih percaya Azure)
            "category_name": cat_result.get("category_name", "Uncategorized"),
            "category_type": cat_result.get("category_type", "Expense"),
            "ai_confidence": float(cat_result.get("ai_confidence", 0.99)),
            "is_ocr_success": True
        }

    except Exception as e:
        logger.error(f"OCR Error: {e}")
        return {"error": str(e), "is_ocr_success": False}