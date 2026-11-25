"""CRM storage layer (SQLite for the MVP; the schema maps 1:1 to PostgreSQL in production)."""
import json
import sqlite3
import threading
from datetime import datetime

from .config import DB_PATH, TENANT_ID

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    name TEXT, phone TEXT, email TEXT,
    budget TEXT, need TEXT, timeline TEXT, timeline_months REAL,
    authority TEXT, location_pref TEXT,
    language TEXT DEFAULT 'en',
    score INTEGER DEFAULT 0, category TEXT DEFAULT 'Cold',
    sentiment_avg REAL DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    lead_id INTEGER REFERENCES leads(id),
    channel TEXT DEFAULT 'web',
    external_id TEXT,
    language TEXT DEFAULT 'en',
    status TEXT DEFAULT 'active',
    started_at TEXT, ended_at TEXT, duration_s INTEGER,
    state_json TEXT DEFAULT '{}',
    summary TEXT, outcome TEXT, overall_sentiment TEXT,
    insights_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES calls(id),
    role TEXT, text TEXT,
    sentiment REAL, intent TEXT, latency_ms INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    lead_id INTEGER REFERENCES leads(id),
    call_id INTEGER REFERENCES calls(id),
    slot_start TEXT UNIQUE,
    kind TEXT DEFAULT 'Site visit',
    status TEXT DEFAULT 'confirmed',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_messages_call ON messages(call_id);
"""

LEAD_FIELDS = ["name", "phone", "email", "budget", "need", "timeline",
               "timeline_months", "authority", "location_pref"]


def now():
    return datetime.now().isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)


def _one(sql, args=()):
    with conn() as c:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None


def _all(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _exec(sql, args=()):
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


# ---------- leads ----------
def create_lead(language="en", phone=None):
    return _exec("INSERT INTO leads (tenant_id, language, phone, created_at, updated_at) VALUES (?,?,?,?,?)",
                 (TENANT_ID, language, phone, now(), now()))


def get_lead(lead_id):
    return _one("SELECT * FROM leads WHERE id=?", (lead_id,))


def find_lead_by_phone(phone):
    if not phone:
        return None
    return _one("SELECT * FROM leads WHERE phone=? AND tenant_id=? ORDER BY id LIMIT 1", (phone, TENANT_ID))


def update_lead(lead_id, fields: dict):
    fields = {k: v for k, v in fields.items() if k in LEAD_FIELDS + ["score", "category", "sentiment_avg", "language"]}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    _exec(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?", (*fields.values(), now(), lead_id))


def merge_leads(keep_id, drop_id):
    """Returning caller recognised mid-call: copy new info into the old lead, re-point records."""
    keep, drop = get_lead(keep_id), get_lead(drop_id)
    updates = {k: drop[k] for k in LEAD_FIELDS if drop.get(k) and not keep.get(k)}
    update_lead(keep_id, updates)
    _exec("UPDATE calls SET lead_id=? WHERE lead_id=?", (keep_id, drop_id))
    _exec("UPDATE appointments SET lead_id=? WHERE lead_id=?", (keep_id, drop_id))
    _exec("DELETE FROM leads WHERE id=?", (drop_id,))


def list_leads():
    return _all("SELECT * FROM leads WHERE tenant_id=? ORDER BY score DESC, updated_at DESC", (TENANT_ID,))


# ---------- calls ----------
def create_call(lead_id, language, channel="web", external_id=None):
    return _exec("INSERT INTO calls (tenant_id, lead_id, language, channel, external_id, started_at) VALUES (?,?,?,?,?,?)",
                 (TENANT_ID, lead_id, language, channel, external_id, now()))


def get_call(call_id):
    c = _one("SELECT * FROM calls WHERE id=?", (call_id,))
    if c:
        c["state"] = json.loads(c.get("state_json") or "{}")
        c["insights"] = json.loads(c.get("insights_json") or "{}")
    return c


def get_call_by_external(external_id):
    r = _one("SELECT id FROM calls WHERE external_id=?", (external_id,))
    return get_call(r["id"]) if r else None


def save_call_state(call_id, state: dict):
    _exec("UPDATE calls SET state_json=? WHERE id=?", (json.dumps(state), call_id))


def finish_call(call_id, summary, outcome, overall_sentiment, insights: dict):
    call = get_call(call_id)
    started = datetime.fromisoformat(call["started_at"])
    duration = int((datetime.now() - started).total_seconds())
    _exec("""UPDATE calls SET status='completed', ended_at=?, duration_s=?, summary=?, outcome=?,
             overall_sentiment=?, insights_json=? WHERE id=?""",
          (now(), duration, summary, outcome, overall_sentiment, json.dumps(insights), call_id))


def list_calls(limit=50):
    rows = _all("""SELECT calls.*, leads.name AS lead_name, leads.phone AS lead_phone
                   FROM calls LEFT JOIN leads ON leads.id = calls.lead_id
                   WHERE calls.tenant_id=? ORDER BY calls.id DESC LIMIT ?""", (TENANT_ID, limit))
    for r in rows:
        r["insights"] = json.loads(r.get("insights_json") or "{}")
        r.pop("state_json", None)
    return rows


def previous_summaries(lead_id, exclude_call_id, limit=2):
    return _all("""SELECT started_at, summary, outcome FROM calls
                   WHERE lead_id=? AND id<>? AND summary IS NOT NULL ORDER BY id DESC LIMIT ?""",
                (lead_id, exclude_call_id, limit))


# ---------- messages ----------
def add_message(call_id, role, text, sentiment=None, intent=None, latency_ms=None):
    return _exec("INSERT INTO messages (call_id, role, text, sentiment, intent, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                 (call_id, role, text, sentiment, intent, latency_ms, now()))


def get_messages(call_id, limit=None):
    rows = _all("SELECT * FROM messages WHERE call_id=? ORDER BY id", (call_id,))
    return rows[-limit:] if limit else rows


def lead_sentiment_avg(lead_id):
    r = _one("""SELECT AVG(m.sentiment) AS avg FROM messages m JOIN calls c ON c.id = m.call_id
                WHERE c.lead_id=? AND m.role='user' AND m.sentiment IS NOT NULL""", (lead_id,))
    return round(r["avg"], 2) if r and r["avg"] is not None else 0.0


# ---------- appointments ----------
def booked_slots():
    return {r["slot_start"] for r in _all("SELECT slot_start FROM appointments WHERE status='confirmed'")}


def create_appointment(lead_id, call_id, slot_start, kind="Site visit"):
    try:
        return _exec("INSERT INTO appointments (tenant_id, lead_id, call_id, slot_start, kind, created_at) VALUES (?,?,?,?,?,?)",
                     (TENANT_ID, lead_id, call_id, slot_start, kind, now()))
    except sqlite3.IntegrityError:
        return None  # slot already taken (double-booking protection)

def cancel_appointment(appt_id):
    """Mark as rescheduled and free the slot (slot_start is UNIQUE, so tag the old value)."""
    _exec("UPDATE appointments SET status='rescheduled', slot_start=slot_start || '#old' || id WHERE id=?", (appt_id,))

def lead_appointment(lead_id):
    return _one("SELECT * FROM appointments WHERE lead_id=? AND status='confirmed' ORDER BY id DESC LIMIT 1", (lead_id,))


def list_appointments():
    return _all("""SELECT a.*, l.name AS lead_name, l.phone AS lead_phone FROM appointments a
                   LEFT JOIN leads l ON l.id = a.lead_id WHERE a.tenant_id=? AND a.status='confirmed'
                   ORDER BY a.slot_start""", (TENANT_ID,))