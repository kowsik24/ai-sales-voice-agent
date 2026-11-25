"""Run:  python check_gemini.py
Shows which Gemini models your key can use and tests the configured model."""
from app import llm
from app.config import GEMINI_API_KEY, GEMINI_FALLBACK_MODEL, GEMINI_MODEL

if not GEMINI_API_KEY:
    raise SystemExit("❌ GEMINI_API_KEY is empty in .env")

print("Configured:", GEMINI_MODEL, "| fallback:", GEMINI_FALLBACK_MODEL)
print("Flash models available to your key:", llm.discover_models() or "(could not list)")
try:
    out = llm.generate_json("Return JSON only.", 'Say hello as {"reply": "..."}')
    print("✅ Gemini works with model:", llm.current_model(), "->", out)
except llm.LLMUnavailable as e:
    print("❌ Gemini failed:", e)