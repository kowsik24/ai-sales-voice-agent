# Demo Script & Interview Prep

## Before the interview (night before / morning)
1. `pip install -r requirements.txt`, put your Gemini key in `.env`, then run `uvicorn app.main:app --reload`.
2. Open Chrome at http://localhost:8000 and allow the microphone. Do one full test call.
3. Delete `data/crm.db` for a clean demo, or keep 2–3 test calls so the dashboard has data.
4. Keep the **typing box** as backup if the room is noisy or the mic fails.
5. Use a headset or earphones, so the agent's voice doesn't loop back into the mic.

## 5-minute presentation flow
1. **Problem (30 s):** leads go cold because tele-calling is slow, costly and office-hours only. The agent answers every lead instantly, 24×7, multilingual.
2. **Architecture (60 s):** show `docs/architecture.png`. Explain the pipeline: STT → orchestrator (RAG + Gemini JSON) → scoring/booking → CRM → TTS.
3. **Live demo (2 min):** say these lines one by one:
   - "Hi, I'm looking for a 2 BHK in Whitefield. What's the price?" → RAG answer. Point at the sources panel.
   - "My budget is around 90 lakhs, I want to buy in the next 3 months." → watch the score jump.
   - "Hmm, that's a bit expensive." → objection handled with the payment plan and offer; sentiment turns negative.
   - "Okay, can I visit the site this weekend?" → slots offered.
   - "Saturday 11 works. My name is Rahul, number 98765 43210." → booked; lead becomes **Hot**.
   - "That's all, thanks, bye." → auto-ends and shows the AI summary.
   - Open **/dashboard**: KPIs, lead table, appointment, transcript, objections.
4. **Multilingual (30 s):** switch to Hindi or Telugu and ask "प्राइस क्या है?"
5. **Memory (15 s):** start a new call with phone 9876543210. It says "Welcome back, Rahul".
6. **Scale, security, cost (60 s):** use `ARCHITECTURE.md` sections 3–6.

## Likely questions and short answers
- **Why one LLM call per turn?** Latency and cost. Gemini returns reply, sentiment, intent, fields and action as one JSON object.
- **How do you stop the LLM booking a wrong slot?** It only proposes. `booking.book()` checks the slot is in the free list, and the DB has a UNIQUE constraint.
- **Why is the lead score rule-based?** Explainable to sales managers, deterministic, and can't be manipulated by the caller. The LLM only extracts facts.
- **What if Gemini is down or rate-limited?** It falls back to the second model, then to the rule-based engine. The call continues.
- **How is RAG multilingual?** Gemini embeddings are cross-lingual, so a Hindi question matches English docs. TF-IDF is the offline fallback.
- **Latency now and in production?** Shown per turn in the UI (about 1–2 s with Gemini). Production plan: streaming STT/LLM/TTS + VAD + barge-in to get under 1 s.
- **Hallucination?** The prompt says to use only KB facts and to say "a specialist will confirm" otherwise; temperature is moderate; sources are logged.
- **Multi-tenant?** `tenant_id` on every table now. Production uses Postgres RLS and per-tenant vector namespaces and API keys.

## Live-modification recipes (they may ask you to change something)
| Ask | Where to change |
|---|---|
| Add a new FAQ / product info | Edit `knowledge/01_project.md`, then `POST /api/knowledge/reload` (or restart) |
| Change agent name or company | `.env`: `AGENT_NAME`, `COMPANY_NAME` |
| Add a language (e.g. Tamil) | `config.py` `LANGUAGES`: add `"ta": ("Tamil", "ta-IN", "ta-IN-PallaviNeural")`, plus a greeting in `agent.py` `GREETINGS` and `RETURNING` |
| Change scoring weights / Hot threshold | `scoring.py` `score_lead()` |
| Collect a new field (e.g. "loan needed") | Add a column in `db.py` SCHEMA + `LEAD_FIELDS`, add it to the `lead_updates` JSON in `SYSTEM_PROMPT`, show it in `app.js` `FIELD_LABELS` (delete `data/crm.db` after schema change) |
| Change office hours / closed day | `config.py` `BUSINESS_HOURS`, `booking.py` (Tuesday rule) |
| Make replies shorter / more formal | `SYSTEM_PROMPT` rule 1 in `agent.py` |
| Switch to another LLM | Only `llm.generate_json()` needs to change |

## Debugging checklist
- **Mic not working:** use Chrome or Edge, on `localhost` (not a LAN IP over http). Check the site's mic permission.
- **Engine shows "rule-based":** `.env` is missing the key, or the server wasn't restarted after editing `.env`.
- **429 errors in the terminal:** free-tier rate limit. Set `GEMINI_MODEL=gemini-2.5-flash-lite` or wait a minute.
- **No voice output:** untick "Neural voice" to use the browser voice. Edge TTS needs internet.
- **Model not found:** Google renames models. Update `GEMINI_MODEL` in `.env` to a current one listed in AI Studio.
