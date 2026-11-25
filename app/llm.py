"""LLM layer with layered fallbacks, so a live call never stalls.

Order: Gemini models (configured -> older/less busy -> discovered) -> Groq (optional, free)
-> LLMUnavailable (agent switches to rule-based).
A model that returns 503 (busy) / 429 (rate limit) is skipped for COOLDOWN_S seconds,
and the whole chain has a time budget, so one turn never waits too long.
"""
import json
import os
import re
import time

import requests

from .config import GEMINI_API_KEY, GEMINI_EMBED_MODEL, GEMINI_FALLBACK_MODEL, GEMINI_MODEL

EXTRA_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_EXTRA_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-3.1-flash-lite").split(",") if m.strip()]
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()
GROQ_PREFERRED = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
COOLDOWN_S = 60
TIME_BUDGET_S = 12

_client = None
_working_model = None
_discovered = None
_groq_model = None
_cooldown = {}          # model -> time until which we skip it
last_error = ""


class LLMUnavailable(Exception):
    pass


def available():
    return bool(GEMINI_API_KEY or GROQ_API_KEY)


def client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        _client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=8000))
    return _client


def discover_models():
    """Text 'flash' models this key can use, newest first (skips tts/image/live/audio)."""
    global _discovered
    if _discovered is None:
        try:
            names = []
            for m in client().models.list():
                name = m.name.split("/")[-1]
                actions = getattr(m, "supported_actions", None) or []
                if "flash" in name and (not actions or "generateContent" in actions) and not re.search(
                        r"tts|image|live|audio|embedding|preview|exp|thinking", name):
                    names.append(name)
            _discovered = sorted(set(names), reverse=True)
        except Exception as e:
            print(f"[LLM] model discovery failed: {e}")
            _discovered = []
    return _discovered


def _candidates():
    seen, out = set(), []
    working = _working_model if _working_model and not _working_model.startswith("groq/") else None
    for m in [working, GEMINI_MODEL, GEMINI_FALLBACK_MODEL, *EXTRA_MODELS, *discover_models()]:
        if m and m not in seen:
            seen.add(m); out.append(m)
    now = time.time()
    ready = [m for m in out if _cooldown.get(m, 0) <= now]
    return ready or out          # if everything is cooling down, try anyway


def _parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _thinking_cfg(model):
    from google.genai import types
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)          # 2.x: thinking off
    return types.ThinkingConfig(thinking_level="MINIMAL")        # 3.x: minimal thinking = low latency


def _try_gemini(model, system, prompt, temperature, max_tokens):
    from google.genai import types
    base = dict(system_instruction=system, temperature=temperature,
                max_output_tokens=max_tokens, response_mime_type="application/json")
    try:
        cfg = types.GenerateContentConfig(**base, thinking_config=_thinking_cfg(model))
        resp = client().models.generate_content(model=model, contents=prompt, config=cfg)
    except Exception as e:
        if "think" not in str(e).lower():
            raise
        resp = client().models.generate_content(model=model, contents=prompt,
                                                config=types.GenerateContentConfig(**base))
    return _parse_json(resp.text)


# ---------------- Groq (optional free backup, OpenAI-compatible API) ----------------
def _groq_pick_model():
    global _groq_model
    if _groq_model:
        return _groq_model
    if GROQ_MODEL:
        _groq_model = GROQ_MODEL
        return _groq_model
    r = requests.get("https://api.groq.com/openai/v1/models",
                     headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=6)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    _groq_model = next((m for m in GROQ_PREFERRED if m in ids), None) or \
        next((m for m in ids if re.search(r"llama|gpt-oss|qwen", m) and not re.search(r"guard|whisper|tts", m)), None)
    if not _groq_model:
        raise RuntimeError("no suitable Groq model found")
    print(f"[LLM] Groq model: {_groq_model}")
    return _groq_model


def _try_groq(system, prompt, temperature, max_tokens):
    model = _groq_pick_model()
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                      json={"model": model, "temperature": temperature, "max_tokens": max_tokens,
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": system + "\nRespond with JSON only."},
                                         {"role": "user", "content": prompt}]},
                      timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Groq {r.status_code}: {r.text[:150]}")
    return _parse_json(r.json()["choices"][0]["message"]["content"]), f"groq/{model}"


# ---------------- public API ----------------
def generate_json(system: str, prompt: str, temperature=0.5, max_tokens=1000) -> dict:
    global _working_model, last_error
    if not available():
        last_error = "No GEMINI_API_KEY / GROQ_API_KEY in .env"
        raise LLMUnavailable(last_error)
    start, tried = time.time(), []

    if GEMINI_API_KEY:
        for model in _candidates():
            if time.time() - start > TIME_BUDGET_S:
                break
            tried.append(model)
            try:
                out = _try_gemini(model, system, prompt, temperature, max_tokens)
                if _working_model != model:
                    print(f"[LLM] using model: {model}")
                _working_model, last_error = model, ""
                return out
            except Exception as e:
                msg = str(e)
                last_error = f"{model}: {msg[:160]}"
                print(f"[LLM] {last_error}")
                _cooldown[model] = time.time() + (COOLDOWN_S if re.search(r"503|429|UNAVAILABLE|RESOURCE_EXHAUSTED|disconnect|timed? ?out", msg, re.I) else 600)
                if _working_model == model:
                    _working_model = None

    if GROQ_API_KEY:
        try:
            out, name = _try_groq(system, prompt, temperature, max_tokens)
            if _working_model != name:
                print(f"[LLM] using model: {name}")
            _working_model, last_error = name, ""
            return out
        except Exception as e:
            last_error = f"groq: {str(e)[:160]}"
            print(f"[LLM] {last_error}")
            tried.append("groq")

    raise LLMUnavailable(f"tried {tried}; last error: {last_error}")


def current_model():
    return _working_model or GEMINI_MODEL


def embed(texts, task="RETRIEVAL_DOCUMENT"):
    from google.genai import types
    out = []
    for i in range(0, len(texts), 90):  # API batch limit is 100
        resp = client().models.embed_content(
            model=GEMINI_EMBED_MODEL, contents=texts[i:i + 90],
            config=types.EmbedContentConfig(task_type=task))
        out.extend(e.values for e in resp.embeddings)
    return out