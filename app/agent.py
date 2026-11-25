"""The sales agent brain: one function per call lifecycle step.

start_call()  -> creates/recognises lead, returns greeting
handle_turn() -> RAG retrieve -> LLM (JSON: reply + sentiment + extracted lead fields + action)
                 -> CRM update -> booking -> lead score
end_call()    -> LLM call summary + business insights
"""
import json
import re
import time

from . import booking, db, llm
from .config import AGENT_NAME, COMPANY_NAME, LANGUAGES
from .rag import kb
from .scoring import lexicon_sentiment, score_lead, sentiment_label

GREETINGS = {
    "en": "Hi{name}! This is {agent} from {company}. Thanks for your interest in our homes in Whitefield, Bengaluru. How can I help you today?",
    "hi": "नमस्ते{name}! मैं {company} से {agent} बोल रही हूँ। व्हाइटफ़ील्ड, बेंगलुरु में हमारे घरों में रुचि दिखाने के लिए धन्यवाद। मैं आपकी कैसे मदद कर सकती हूँ?",
    "te": "నమస్కారం{name}! నేను {company} నుండి {agent} మాట్లాడుతున్నాను. బెంగళూరు వైట్‌ఫీల్డ్‌లో మా ఇళ్లపై ఆసక్తి చూపినందుకు ధన్యవాదాలు. నేను మీకు ఎలా సహాయం చేయగలను?",
}
PHONE_RETRY = {
    "en": "Sorry, I need your 10-digit mobile number to confirm the booking. Could you please say it slowly, digit by digit?",
    "hi": "माफ़ कीजिए, बुकिंग के लिए मुझे आपका 10 अंकों का मोबाइल नंबर चाहिए। क्या आप धीरे-धीरे दोबारा बता सकते हैं?",
    "te": "క్షమించండి, బుకింగ్ కోసం మీ 10 అంకెల మొబైల్ నంబర్ కావాలి. దయచేసి నెమ్మదిగా మళ్ళీ చెప్పగలరా?",
}

RETURNING = {
    "en": "Welcome back{name}! This is {agent} from {company}. Good to hear from you again. How can I help you today?",
    "hi": "फिर से स्वागत है{name}! मैं {company} से {agent} बोल रही हूँ। आज मैं आपकी कैसे मदद कर सकती हूँ?",
    "te": "మళ్ళీ స్వాగతం{name}! నేను {company} నుండి {agent} మాట్లాడుతున్నాను. ఈ రోజు నేను మీకు ఎలా సహాయం చేయగలను?",
}

SYSTEM_PROMPT = """You are {agent}, a warm, professional sales voice agent for {company}, a residential project in Whitefield, Bengaluru.
You are on a LIVE VOICE CALL. Your text is converted to speech.

RULES
1. Reply in {language}. Natural spoken style, 1-3 short sentences, no lists, no markdown, no emojis. Say prices like "85 lakh".
2. Use ONLY facts from KNOWLEDGE. If something is not there (payment method, discount, address, number), say a property specialist will confirm it. Never invent prices, offers, EMIs, payment modes or dates. For EMI, quote the nearest row of the EMI table in KNOWLEDGE and say it is approximate.
3. Qualify the lead naturally, one question at a time, never interrogate: requirement (1/2/3 BHK), budget, purchase timeline, who decides, name, phone number.
4. BUDGET FIT: if the caller's budget is below what they asked for, do not just push the expensive flat. Use the area-wise budget guide in KNOWLEDGE to suggest the GreenNest project and area that fits, with its starting price and address, and ask if they want to visit it. Mention 1-2 options, not a long list.
5. When the caller shows interest, offer a site visit using AVAILABLE SLOTS (mention at most 3, in friendly words).
6. PHONE NUMBER: an Indian mobile number has exactly 10 digits and starts with 6, 7, 8 or 9. Callers often say it in parts ("98765 43210"). If what you heard does not make exactly 10 such digits, politely ask them to repeat it slowly. Once you have 10 digits, read it back in groups ("9 8 7 6 5, 4 3 2 1 0") and ask them to confirm. Put only those 10 digits in lead_updates.phone.
7. BOOKING: you need the caller's name and a confirmed 10-digit phone number. When the caller agrees to a specific slot and you have both, set action.type="book_appointment" with the EXACT slot string from AVAILABLE SLOTS and action.project = the project name, and confirm it in the reply.
8. Handle objections (price, location, trust) with empathy and facts from KNOWLEDGE (payment plan, loans, EMI table, cheaper projects).
9. If the caller wants to stop or says goodbye, thank them and set action.type="end_call".
10. Remember everything in LEAD PROFILE and PREVIOUS CALLS; never ask again for something already known.

Return ONLY JSON with this shape:
{{
 "reply": "what you say next",
 "sentiment_score": number from -1 (very negative) to 1 (very positive) for the CALLER's latest message,
 "intent": one of "inquiry","pricing","objection","booking","qualification","smalltalk","not_interested","end_call",
 "lead_updates": {{"name": str|null, "phone": str|null, "email": str|null, "budget": str|null, "need": str|null,
                  "timeline": str|null, "timeline_months": number|null, "authority": str|null, "location_pref": str|null}},
 "action": {{"type": "none"|"book_appointment"|"end_call", "slot": "YYYY-MM-DD HH:MM" or null, "project": str|null}}
}}
In lead_updates include ONLY facts the caller stated in this conversation (others null). Write lead_updates values in English."""

SUMMARY_PROMPT = """You are a sales analyst. Analyse this sales call transcript and the lead profile.
Return ONLY JSON:
{
 "summary": "3-4 sentence summary in English",
 "outcome": one of "appointment_booked","interested_follow_up","needs_more_info","not_interested",
 "overall_sentiment": "positive"|"neutral"|"negative",
 "key_interests": [short strings],
 "objections": [short strings],
 "next_steps": [short actionable strings for the sales team],
 "follow_up_priority": "high"|"medium"|"low"
}"""


# ------------------------------------------------------------------ lifecycle
def start_call(language="en", phone=None, channel="web", external_id=None):
    language = language if language in LANGUAGES else "en"
    phone = _clean_phone(phone)
    lead = db.find_lead_by_phone(phone) if phone else None
    returning = lead is not None
    lead_id = lead["id"] if lead else db.create_lead(language, phone)
    call_id = db.create_call(lead_id, language, channel, external_id)
    lead = db.get_lead(lead_id)

    name = f", {lead['name']}" if returning and lead.get("name") else ""
    template = RETURNING[language] if returning else GREETINGS[language]
    greeting = template.format(name=name, agent=AGENT_NAME, company=COMPANY_NAME)
    db.add_message(call_id, "assistant", greeting)
    return {"call_id": call_id, "lead": _lead_view(lead), "greeting": greeting,
            "returning": returning, "mode": f"gemini ({llm.current_model()})" if llm.available() else "rule-based"}


def handle_turn(call_id, user_text):
    t0 = time.time()
    call = db.get_call(call_id)
    if not call or call["status"] != "active":
        raise ValueError("Call not active")
    lead = db.get_lead(call["lead_id"])
    state = call["state"]
    history = db.get_messages(call_id, limit=14)

    # 1) RAG: retrieve with the user text + last agent turn for context
    # short replies like "yes, tell me more" need the agent's last line as context
    last_agent = next((m["text"] for m in reversed(history) if m["role"] == "assistant"), "")
    query = user_text if len(user_text.split()) >= 4 else f"{user_text} {last_agent[-150:]}"
    docs = kb.search(query, k=4)
    slots = booking.available_slots()

    # 2) LLM (or rule-based fallback)
    mode = "gemini"
    try:
        result = _llm_turn(call, lead, history, user_text, docs, slots)
    except llm.LLMUnavailable:
        mode = "rule-based"
        result = _rule_based_turn(user_text, lead, state, docs, slots,
                                  has_appt=db.lead_appointment(lead["id"]) is not None)

    reply = (result.get("reply") or "").strip() or "Sorry, could you say that again?"

    # Server-side phone validation: never trust a malformed number from STT/LLM
    said_phone = (result.get("lead_updates") or {}).get("phone")
    if said_phone and not _clean_phone(said_phone):
        result["lead_updates"]["phone"] = None
        reply = PHONE_RETRY.get(call["language"], PHONE_RETRY["en"])
        if (result.get("action") or {}).get("type") == "book_appointment":
            state["pending_slot"] = booking.normalise(result["action"].get("slot"))
            result["action"] = {"type": "none"}
    sentiment = result.get("sentiment_score")
    if not isinstance(sentiment, (int, float)):
        sentiment = lexicon_sentiment(user_text)
    intent = result.get("intent", "inquiry")

    # 3) CRM: save message + extracted fields (handles returning callers)
    db.add_message(call_id, "user", user_text, sentiment=sentiment, intent=intent)
    lead_id = _apply_lead_updates(lead["id"], result.get("lead_updates") or {})

    # 4) Actions: booking / end call
    action = result.get("action") or {}
    appointment, end = None, False
    if action.get("type") == "book_appointment" and not db.get_lead(lead_id).get("phone"):
        state["pending_slot"] = booking.normalise(action.get("slot"))
        reply = PHONE_RETRY.get(call["language"], PHONE_RETRY["en"])     # no valid phone -> don't book yet
    elif action.get("type") == "book_appointment":
        existing = db.lead_appointment(lead_id)
        wanted = booking.normalise(action.get("slot"))
        if existing and existing["slot_start"] == wanted:
            ok, value = True, wanted                      # same slot again: nothing to change
        else:
            kind = f"Site visit - {action['project']}" if action.get("project") else "Site visit"
            ok, value = booking.book(lead_id, call_id, action.get("slot"), kind)
            if ok and existing:
                db.cancel_appointment(existing["id"])      # reschedule: free the old slot
        if ok:
            appointment = {"slot": value, "pretty": booking.pretty(value)}
            state.pop("pending_slot", None)
        else:
            alts = ", ".join(booking.pretty(s) for s in value)
            reply = f"Sorry, that slot is no longer available. I can offer {alts}. Which one works for you?"
            state["offered_slots"] = value
    elif action.get("type") == "end_call":
        end = True

    # 5) Score
    lead = db.get_lead(lead_id)
    appt = db.lead_appointment(lead_id)
    s_avg = db.lead_sentiment_avg(lead_id)
    score, category, breakdown = score_lead(lead, appt is not None, s_avg)
    db.update_lead(lead_id, {"score": score, "category": category, "sentiment_avg": s_avg})

    latency = int((time.time() - t0) * 1000)
    db.add_message(call_id, "assistant", reply, intent=intent, latency_ms=latency)
    db.save_call_state(call_id, state)

    return {
        "reply": reply, "intent": intent, "latency_ms": latency, "end_call": end,
        "mode": f"gemini ({llm.current_model()})" if mode == "gemini" else "rule-based",
        "llm_error": llm.last_error if mode == "rule-based" else "",
        "sentiment": {"score": round(float(sentiment), 2), "label": sentiment_label(float(sentiment))},
        "lead": _lead_view(db.get_lead(lead_id), breakdown),
        "appointment": appointment or (appt and {"slot": appt["slot_start"], "pretty": booking.pretty(appt["slot_start"])}),
        "sources": [{"title": d["title"], "source": d["source"], "score": d["score"]} for d in docs],
        "rag_mode": kb.mode,
    }


def end_call(call_id):
    call = db.get_call(call_id)
    if not call:
        raise ValueError("Call not found")
    if call["status"] == "completed":
        return {"summary": call["summary"], "outcome": call["outcome"], **call["insights"]}
    lead = db.get_lead(call["lead_id"])
    msgs = db.get_messages(call_id)
    transcript = "\n".join(f"{'Agent' if m['role'] == 'assistant' else 'Caller'}: {m['text']}" for m in msgs)
    appt = db.lead_appointment(lead["id"])
    try:
        data = llm.generate_json(SUMMARY_PROMPT,
                                 f"LEAD PROFILE: {json.dumps(_lead_public(lead))}\n"
                                 f"APPOINTMENT: {appt['slot_start'] if appt else 'none'}\n\nTRANSCRIPT:\n{transcript}",
                                 temperature=0.2)
    except llm.LLMUnavailable:
        data = _rule_based_summary(lead, msgs, appt)
    if appt:
        data["outcome"] = "appointment_booked"
    insights = {k: data.get(k, []) for k in ("key_interests", "objections", "next_steps")}
    insights["follow_up_priority"] = data.get("follow_up_priority", "medium")
    db.finish_call(call_id, data.get("summary", ""), data.get("outcome", "needs_more_info"),
                   data.get("overall_sentiment", "neutral"), insights)
    return {"summary": data.get("summary"), "outcome": data.get("outcome"),
            "overall_sentiment": data.get("overall_sentiment"), **insights,
            "lead": _lead_view(db.get_lead(lead["id"]))}


# ------------------------------------------------------------------ LLM path
def _llm_turn(call, lead, history, user_text, docs, slots):
    language = LANGUAGES[call["language"]][0]
    system = SYSTEM_PROMPT.format(agent=AGENT_NAME, company=COMPANY_NAME, language=language)
    prev = db.previous_summaries(lead["id"], call["id"])
    appt = db.lead_appointment(lead["id"])
    knowledge = "\n".join(f"[{d['title']}] {d['text']}" for d in docs)
    slot_text = "\n".join(f"{s}  ({booking.pretty(s)})" for s in slots[:30])
    convo = "\n".join(f"{'Agent' if m['role'] == 'assistant' else 'Caller'}: {m['text']}" for m in history)
    prompt = f"""LEAD PROFILE (already known): {json.dumps(_lead_public(lead))}
PREVIOUS CALLS: {json.dumps(prev) if prev else 'none'}
EXISTING APPOINTMENT: {booking.pretty(appt['slot_start']) if appt else 'none'}

KNOWLEDGE:
{knowledge}

AVAILABLE SLOTS:
{slot_text}

CONVERSATION SO FAR:
{convo}

Caller (latest): {user_text}"""
    return llm.generate_json(system, prompt)


# ------------------------------------------------------------------ rule-based fallback
QUESTIONS = [
    ("need", "Are you looking for a 2 BHK or a 3 BHK home?"),
    ("budget", "What budget range do you have in mind?"),
    ("timeline", "When are you planning to buy, roughly?"),
    ("name", "May I know your name, please?"),
    ("phone", "What's the best phone number to reach you?"),
]
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "first": 0, "second": 1, "third": 2}


def extract_fields(text):
    """Regex extraction used by the fallback agent (and handy as a unit-testable baseline)."""
    out = {}
    joined = re.sub(r"(?<=\d)[\s-](?=\d)", "", text)
    if m := re.search(r"(?<!\d)(?:\+?91)?([6-9]\d{9})(?!\d)", joined):
        out["phone"] = m.group(1)
    if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        out["email"] = m.group(0)
    if m := re.search(r"\b(?:my name is|this is|call me|i am|i'm)\s+([A-Za-z]+(?:\s[A-Z][a-z]+)?)", text, re.I):
        cand = m.group(1).strip()
        if cand.split()[0].lower() not in {"looking", "interested", "planning", "not", "fine", "good", "from", "a", "the",
                                             "busy", "okay", "ok", "calling", "searching", "thinking", "very", "just"}:
            out["name"] = cand.title()
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|l\b|crores?|cr\b)", text, re.I):
        unit = "crore" if m.group(2).lower().startswith("c") else "lakh"
        out["budget"] = f"{m.group(1)} {unit}"
    if m := re.search(r"\b([1-4]|one|two|three|four)\s*-?\s*bhk\b", text, re.I):
        n = m.group(1).lower()
        out["need"] = f"{WORD_NUM.get(n, n)} BHK"
    elif "villa" in text.lower():
        out["need"] = "Villa"
    t = text.lower()
    if m := re.search(r"(\d+|one|two|three|six)\s*(months?|years?)", t):
        n = {"one": 1, "two": 2, "three": 3, "six": 6}.get(m.group(1), None) or float(m.group(1))
        months = n * (12 if m.group(2).startswith("year") else 1)
        out["timeline"], out["timeline_months"] = m.group(0), months
    elif re.search(r"immediate|asap|right away|this month|next month", t):
        out["timeline"], out["timeline_months"] = "immediately / next month", 1
    elif "next year" in t:
        out["timeline"], out["timeline_months"] = "next year", 12
    if re.search(r"\b(wife|husband|parents|family|spouse)\b", t):
        out["authority"] = "joint decision with family"
    elif re.search(r"\b(i decide|my decision|myself)\b", t):
        out["authority"] = "self"
    return out


def _rule_based_turn(text, lead, state, docs, slots, has_appt=False):
    """Simple deterministic agent used only when Gemini is unavailable."""
    t = text.lower().strip()
    updates = extract_fields(text)
    asked = state.get("last_asked")

    # A bare answer to the question we just asked ("Krishna Kaushik", "one month")
    if asked == "name" and "name" not in updates and re.fullmatch(r"[a-zA-Z]+(\s[a-zA-Z]+){0,2}", text.strip()) \
            and t not in {"yes", "no", "ok", "okay", "sure", "thank you", "thanks"}:
        updates["name"] = text.strip().title()
    digits = re.sub(r"\D", "", text)
    bad_phone = asked == "phone" and "phone" not in updates and 5 <= len(digits) < 10

    known = {**{k: lead.get(k) for k in db.LEAD_FIELDS}, **updates}
    result = {"lead_updates": updates, "sentiment_score": lexicon_sentiment(text),
              "intent": "qualification", "action": {"type": "none"}}

    def next_question():
        for field, q in QUESTIONS:
            if not known.get(field):
                state["last_asked"] = field
                return q
        state["last_asked"] = None
        if has_appt:
            return "Is there anything else I can help you with?"
        if state.get("visit_declined"):
            return "Do you have any other questions about the project?"
        return "Would you like to book a free site visit?"

    negative = re.search(r"\b(no|not|don't|dont|do not|nahi|nope)\b", t)
    wants_booking = re.search(r"\b(book|visit|schedule|appointment|reschedule|come and see)\b", t)
    if re.search(r"\b(expensive|costly|too high|too far|not sure|worried|trust)\b", t):
        result["intent"] = "objection"

    # pick a slot from the ones we offered ("first one", "11 am", "yes")
    offered = state.get("offered_slots") or []
    choice = None
    if offered and not negative:
        hour = re.search(r"\b(\d{1,2})\s*(?::00)?\s*(am|pm|a\.m\.|p\.m\.)", t)
        if hour:
            h = int(hour.group(1)) % 12 + (12 if hour.group(2).startswith("p") else 0)
            choice = next((s for s in offered if int(s[11:13]) == h), None)
        if not choice and (m := re.search(r"\b(first|second|third|1st|2nd|3rd)\b", t)):
            idx = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}[m.group(1)]
            choice = offered[min(idx, len(offered) - 1)]
        elif not choice and re.search(r"\b(yes|ok|okay|sure|fine|works|book it)\b", t):
            choice = offered[0]

    ending = re.search(r"\b(bye|goodbye|that's all|thats all|end the call|hang up|not interested)\b", t) or \
        ((has_appt or state.get("visit_declined")) and re.fullmatch(r"(no[, ]*)?(thanks|thank you|nothing)( so much)?[.!]*", t))

    if ending:
        result.update(intent="not_interested" if "not interested" in t else "end_call",
                      reply="Thank you for your time! I'll share the details on WhatsApp. Have a great day.",
                      action={"type": "end_call"})
    elif re.search(r"\b(change|update|wrong|correct)\b.*\b(number|phone|mobile)\b", t) and "phone" not in updates:
        state["last_asked"] = "phone"
        result["reply"] = "Sure, please tell me the new 10-digit mobile number."
    elif bad_phone:
        state["last_asked"] = "phone"
        result["reply"] = "Sorry, that number seems incomplete. Could you please repeat all 10 digits?"
    elif negative and (wants_booking or (state.get("last_asked") is None and not has_appt and not updates)):
        state["visit_declined"] = True
        state.pop("offered_slots", None)
        result.update(intent="booking", reply=f"No problem at all. {next_question()}")
    elif choice or (state.get("pending_slot") and not negative):
        slot = choice or state["pending_slot"]
        if not known.get("name") or not known.get("phone"):
            state["pending_slot"] = slot
            state["last_asked"] = "name" if not known.get("name") else "phone"
            missing = "your name and phone number" if not known.get("name") and not known.get("phone") else \
                "your name" if not known.get("name") else "your phone number"
            result.update(intent="booking", reply=f"Great, {booking.pretty(slot)} it is. May I have {missing} to confirm the booking?")
        else:
            verb = "rescheduled" if has_appt else "booked"
            result.update(intent="booking", action={"type": "book_appointment", "slot": slot},
                          reply=f"Done! Your site visit is {verb} for {booking.pretty(slot)}, {known['name']}. "
                                f"You'll get a confirmation on {known['phone']}. Anything else I can help with?")
            state.pop("offered_slots", None)
            state["last_asked"] = None
    elif wants_booking or (state.get("last_asked") is None and not has_appt and not state.get("visit_declined")
                           and re.fullmatch(r"(yes|yeah|ok|okay|sure)[ ,!.]*.*", t) and len(t.split()) <= 6):
        state["offered_slots"] = slots[:3]
        opts = ", ".join(booking.pretty(s) for s in slots[:3])
        pre = "Sure, let's reschedule." if has_appt else "I'd love to arrange a site visit."
        result.update(intent="booking", reply=f"{pre} I have {opts}. Which one works for you?")
    elif "?" in text or re.search(r"\b(what|how|where|when|which|price|cost|amenit|loan|emi|possession|size|rera|school|offer|discount|parking)\b", t):
        best = docs[0]["text"] if docs else ""
        sentences = re.split(r"(?<=[.!])\s+", best)
        answer = " ".join(sentences[:2]) if best else "Let me have our property specialist confirm that for you."
        if result["intent"] != "objection":
            result["intent"] = "pricing" if re.search(r"price|cost|budget|emi|loan", t) else "inquiry"
        result["reply"] = f"{answer} {next_question()}"
    else:
        ack = "Got it, thank you." if updates else "Okay."
        result["reply"] = f"{ack} {next_question()}"
    return result


def _rule_based_summary(lead, msgs, appt):
    caller = [m for m in msgs if m["role"] == "user"]
    avg = sum((m["sentiment"] or 0) for m in caller) / max(len(caller), 1)
    facts = ", ".join(f"{k}: {lead[k]}" for k in ("need", "budget", "timeline") if lead.get(k)) or "no details captured"
    summary = (f"{lead.get('name') or 'The caller'} enquired about GreenNest Residences ({facts}). "
               + (f"A site visit was booked for {booking.pretty(appt['slot_start'])}." if appt else "No visit booked yet."))
    objections = [m["text"] for m in caller if m["intent"] == "objection"][:3]
    return {"summary": summary, "outcome": "appointment_booked" if appt else "interested_follow_up",
            "overall_sentiment": sentiment_label(avg), "key_interests": [lead["need"]] if lead.get("need") else [],
            "objections": objections,
            "next_steps": ["Send brochure and floor plans on WhatsApp",
                           "Confirm site visit a day before" if appt else "Call back in 2 days to offer a site visit"],
            "follow_up_priority": "high" if appt else "medium"}


# ------------------------------------------------------------------ helpers
def _clean_phone(phone):
    """Valid Indian mobile -> 10 digits, else None. Accepts +91 / 0 prefixes only."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def _apply_lead_updates(lead_id, updates):
    clean = {}
    for k, v in updates.items():
        if k not in db.LEAD_FIELDS or v in (None, "", "null", "unknown"):
            continue
        if k == "timeline_months":
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                continue
        elif k == "phone":
            if p := _clean_phone(v):
                clean[k] = p
        else:
            clean[k] = str(v).strip()
    if not clean:
        return lead_id
    # Contextual memory across calls: if this phone already belongs to a lead, merge into it
    if clean.get("phone"):
        existing = db.find_lead_by_phone(clean["phone"])
        if existing and existing["id"] != lead_id:
            db.update_lead(lead_id, clean)
            db.merge_leads(existing["id"], lead_id)
            return existing["id"]
    db.update_lead(lead_id, clean)
    return lead_id


def _lead_public(lead):
    return {k: lead.get(k) for k in db.LEAD_FIELDS if lead.get(k) is not None}


def _lead_view(lead, breakdown=None):
    if breakdown is None:
        appt = db.lead_appointment(lead["id"])
        _, _, breakdown = score_lead(lead, appt is not None, lead.get("sentiment_avg") or 0)
    return {**lead, "breakdown": breakdown}