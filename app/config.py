"""Central configuration. Everything is read from environment variables (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# --- LLM (Google Gemini, free tier) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# --- Business / tenant ---
TENANT_ID = os.getenv("TENANT_ID", "greennest")
COMPANY_NAME = os.getenv("COMPANY_NAME", "GreenNest Residences")
AGENT_NAME = os.getenv("AGENT_NAME", "Priya")

# --- Paths ---
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "crm.db"))
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
EMBED_CACHE = BASE_DIR / "data" / "embeddings_cache.json"

# --- Booking ---
BUSINESS_HOURS = [10, 11, 12, 15, 16, 17]   # site-visit slot start hours
BOOKING_DAYS_AHEAD = 5

# --- Languages: code -> (display name, browser STT locale, Edge TTS voice) ---
LANGUAGES = {
    "en": ("English", "en-IN", "en-IN-NeerjaNeural"),
    "hi": ("Hindi", "hi-IN", "hi-IN-SwaraNeural"),
    "te": ("Telugu", "te-IN", "te-IN-ShrutiNeural"),
}

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
