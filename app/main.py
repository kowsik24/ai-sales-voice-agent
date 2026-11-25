"""FastAPI entry point: REST API for the voice UI, CRM dashboard, TTS and Twilio telephony."""
from collections import Counter
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, booking, db, llm, telephony
from .config import AGENT_NAME, BASE_DIR, COMPANY_NAME, GEMINI_MODEL, LANGUAGES
from .rag import kb


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    kb.load()
    print(f"[APP] LLM mode: {'Gemini ' + GEMINI_MODEL if llm.available() else 'rule-based (no GEMINI_API_KEY)'}")
    yield


app = FastAPI(title="Autonomous AI Sales Voice Agent", version="1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(telephony.router)


class StartCall(BaseModel):
    language: str = "en"
    phone: str | None = None


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    language: str = "en"


# ---------------- pages ----------------
@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(BASE_DIR / "static" / "dashboard.html")


# ---------------- call API ----------------

@app.get("/api/config")
def config():
    return {"company": COMPANY_NAME, "agent": AGENT_NAME,
            "llm_mode": f"Gemini ({llm.current_model()})" if llm.available() else "Rule-based fallback",
            "llm_error": llm.last_error,
            "rag_mode": kb.mode, "chunks": len(kb.chunks),
            "languages": {k: {"name": v[0], "stt": v[1], "voice": v[2]} for k, v in LANGUAGES.items()}}


@app.post("/api/calls/start")
def start_call(body: StartCall):
    return agent.start_call(body.language, body.phone)


@app.post("/api/calls/{call_id}/message")
def message(call_id: int, body: Message):
    try:
        return agent.handle_turn(call_id, body.text.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/calls/{call_id}/end")
def end(call_id: int):
    try:
        return agent.end_call(call_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/calls/{call_id}")
def get_call(call_id: int):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    call.pop("state_json", None)
    return {"call": call, "lead": db.get_lead(call["lead_id"]), "messages": db.get_messages(call_id)}


@app.get("/api/slots")
def slots():
    return [{"slot": s, "pretty": booking.pretty(s)} for s in booking.available_slots()]


# ---------------- TTS (free neural voices via Edge TTS) ----------------
@app.post("/api/tts")
async def tts(body: TTSRequest):
    import edge_tts
    voice = LANGUAGES.get(body.language, LANGUAGES["en"])[2]
    audio = bytearray()
    try:
        async for chunk in edge_tts.Communicate(body.text, voice, rate="+8%").stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
    except Exception as e:
        raise HTTPException(503, f"TTS unavailable: {e}")
    return Response(bytes(audio), media_type="audio/mpeg")


# ---------------- CRM dashboard + business insights ----------------
@app.get("/api/dashboard")
def dashboard():
    leads, calls, appts = db.list_leads(), db.list_calls(), db.list_appointments()
    done = [c for c in calls if c["status"] == "completed"]
    objections = Counter(o.lower() for c in done for o in c["insights"].get("objections", []))
    interests = Counter(i.lower() for c in done for i in c["insights"].get("key_interests", []))
    outcomes = Counter(c["outcome"] for c in done)
    sentiments = Counter(c["overall_sentiment"] for c in done)
    durations = [c["duration_s"] for c in done if c["duration_s"]]
    return {
        "kpis": {
            "total_calls": len(calls),
            "completed_calls": len(done),
            "total_leads": len(leads),
            "hot_leads": sum(l["category"] == "Hot" for l in leads),
            "warm_leads": sum(l["category"] == "Warm" for l in leads),
            "appointments": len(appts),
            "conversion_rate": round(100 * len({a["lead_id"] for a in appts}) / len(leads), 1) if leads else 0,
            "avg_lead_score": round(sum(l["score"] for l in leads) / len(leads), 1) if leads else 0,
            "avg_call_duration_s": round(sum(durations) / len(durations)) if durations else 0,
        },
        "outcomes": dict(outcomes), "sentiments": dict(sentiments),
        "top_objections": objections.most_common(5), "top_interests": interests.most_common(5),
        "leads": leads, "appointments": [{**a, "pretty": booking.pretty(a["slot_start"])} for a in appts],
        "calls": calls,
    }


@app.post("/api/knowledge/reload")
def reload_knowledge():
    """Edit files in /knowledge and call this - no restart needed (good for live demos)."""
    kb.load()
    return {"chunks": len(kb.chunks), "mode": kb.mode}


@app.get("/api/health")
def health():
    return {"status": "ok", "llm": llm.available(), "rag_chunks": len(kb.chunks)}
