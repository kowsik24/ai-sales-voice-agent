"""Run: pytest -q   (works without a Gemini key - uses the rule-based engine)"""
import os
import tempfile

os.environ["GEMINI_API_KEY"] = ""
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from app import agent, db, llm  # noqa: E402
from app.rag import kb  # noqa: E402
from app.scoring import score_lead  # noqa: E402

llm.available = lambda: False
db.init_db()
kb.load()


def test_extract_fields():
    f = agent.extract_fields("My name is Rahul, I need a 2 BHK, budget 90 lakhs, buying in 3 months, call 98765 43210")
    assert f["name"] == "Rahul" and f["need"] == "2 BHK" and f["budget"] == "90 lakh"
    assert f["timeline_months"] == 3 and f["phone"] == "9876543210"


def test_rag_finds_pricing():
    assert kb.search("what is the price of a 2 BHK")[0]["title"] == "Configurations and pricing"


def test_scoring_hot():
    lead = {"budget": "90 lakh", "need": "2 BHK", "timeline_months": 2, "phone": "9876543210", "authority": "self"}
    score, cat, _ = score_lead(lead, has_appointment=True, sentiment_avg=0.5)
    assert cat == "Hot" and score >= 90


def test_full_call_books_appointment():
    cid = agent.start_call("en")["call_id"]
    agent.handle_turn(cid, "I want a 3 BHK, budget 1.3 crore, within 2 months")
    agent.handle_turn(cid, "Can I book a site visit?")
    agent.handle_turn(cid, "the first one")
    r = agent.handle_turn(cid, "my name is Anita Rao and phone 9123456780")
    assert r["appointment"] is not None and r["lead"]["category"] == "Hot"
    s = agent.end_call(cid)
    assert s["outcome"] == "appointment_booked"


def test_returning_caller_memory():
    r = agent.start_call("en", phone="9123456780")
    assert r["returning"] and "Anita" in r["greeting"]
