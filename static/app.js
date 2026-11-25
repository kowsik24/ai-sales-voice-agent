// Voice loop: Browser STT (Web Speech API) -> FastAPI agent -> TTS (Edge neural voice or browser voice)
const $ = (id) => document.getElementById(id);
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

const S = { config: null, callId: null, active: false, busy: false, speaking: false,
            listening: false, rec: null, audio: null, interimEl: null };

const FIELD_LABELS = { name: "Name", phone: "Phone", email: "Email", need: "Requirement", budget: "Budget",
                       timeline: "Timeline", authority: "Decision maker", location_pref: "Location" };

// ---------- UI helpers ----------
function setStatus(kind, text) {
  $("dot").className = "dot " + (kind || "");
  $("statusText").textContent = text;
}
function bubble(role, text, meta) {
  const el = document.createElement("div");
  el.className = "bubble " + role;
  el.textContent = text;
  if (meta) { const m = document.createElement("span"); m.className = "meta"; m.textContent = meta; el.appendChild(m); }
  $("transcript").appendChild(el);
  $("transcript").scrollTop = 1e9;
  return el;
}
function setCallUI(on) {
  $("startBtn").disabled = on; $("endBtn").disabled = !on;
  $("textInput").disabled = !on; $("sendBtn").disabled = !on; $("micBtn").disabled = !on || !SR;
  $("language").disabled = on; $("phone").disabled = on;
}
async function api(path, body) {
  const r = await fetch(path, { method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function renderLead(lead) {
  if (!lead) return;
  $("score").textContent = lead.score ?? 0;
  $("scoreBar").style.width = (lead.score ?? 0) + "%";
  $("category").className = "badge " + (lead.category || "Cold");
  $("category").textContent = lead.category || "Cold";
  $("leadFields").innerHTML = "";
  for (const [k, label] of Object.entries(FIELD_LABELS)) {
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = lead[k] || "-";
    $("leadFields").append(dt, dd);
  }
  $("breakdown").innerHTML = "";
  (lead.breakdown || []).forEach(b => {
    const li = document.createElement("li");
    li.textContent = `${b.points > 0 ? "+" : ""}${b.points}  ${b.reason}`;
    $("breakdown").appendChild(li);
  });
}

function renderTurn(r) {
  renderLead(r.lead);
  $("sentiment").className = "badge " + r.sentiment.label;
  $("sentiment").textContent = `${r.sentiment.label} (${r.sentiment.score})`;
  $("intent").textContent = r.intent;
  $("latency").textContent = r.latency_ms + " ms";
  $("engine").textContent = `${r.mode} · RAG: ${r.rag_mode}`;
  $("engine").style.color = r.llm_error ? "#dc2626" : "";
  if (r.llm_error) $("engine").textContent += `  ⚠ Gemini failed: ${r.llm_error}`;
  $("appt").innerHTML = r.appointment ? `<div class="appt">📅 Site visit booked: ${r.appointment.pretty}</div>` : "";
  $("sources").innerHTML = "";
  r.sources.forEach(s => {
    const li = document.createElement("li");
    li.innerHTML = `<b></b> <span class="small"></span>`;
    li.querySelector("b").textContent = s.title;
    li.querySelector("span").textContent = `(${s.source}, score ${s.score})`;
    $("sources").appendChild(li);
  });
}

// ---------- Speech output ----------
function stopSpeaking() {
  if (S.audio) { S.audio.pause(); S.audio = null; }
  window.speechSynthesis && speechSynthesis.cancel();
  if (S.resolveSpeak) { S.resolveSpeak(); S.resolveSpeak = null; }
  S.speaking = false;
}
function browserSpeak(text, locale) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = locale; u.rate = 1.03;
    const v = speechSynthesis.getVoices().find(v => v.lang.replace("_", "-").startsWith(locale.slice(0, 2)));
    if (v) u.voice = v;
    u.onend = u.onerror = () => resolve();
    S.resolveSpeak = resolve;
    speechSynthesis.speak(u);
    setTimeout(resolve, 20000); // safety net
  });
}
async function speak(text) {
  const lang = $("language").value;
  const locale = S.config.languages[lang].stt;
  const token = (S.speakToken = (S.speakToken || 0) + 1);
  S.speaking = true;
  stopListening();
  setStatus("speaking", "Agent speaking… (click Talk to interrupt)");
  try {
    if (!$("neural").checked) throw new Error("browser voice selected");
    const r = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ text, language: lang }) });
    if (!r.ok) throw new Error("tts failed");
    const url = URL.createObjectURL(await r.blob());
    if (!S.speaking) return;          // interrupted while loading
    S.audio = new Audio(url);
    await new Promise((res) => { S.resolveSpeak = res; S.audio.onended = S.audio.onerror = res; S.audio.play().catch(res); });
  } catch (e) {
    if (S.speaking) await browserSpeak(text, locale);
  }
  if (token !== S.speakToken || !S.speaking) return;   // interrupted (barge-in) or superseded
  S.speaking = false;
  if (S.active) { setStatus("", "Your turn - click Talk or type"); if ($("handsfree").checked) listen(); }
}

// ---------- Speech input ----------
function stopListening() {
  if (S.rec) { S.rec.onend = null; try { S.rec.abort(); } catch (e) {} S.rec = null; }
  S.listening = false;
  if (S.interimEl) { S.interimEl.remove(); S.interimEl = null; }
}
function listen() {
  if (!SR || !S.active || S.busy || S.speaking || S.listening) return;
  const rec = new SR();
  rec.lang = S.config.languages[$("language").value].stt;
  rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
  let finalText = "";
  rec.onresult = (e) => {
    let interim = "";
    for (const res of e.results) { if (res.isFinal) finalText += res[0].transcript; else interim += res[0].transcript; }
    if (!S.interimEl) S.interimEl = bubble("user interim", "");
    S.interimEl.textContent = finalText || interim;
  };
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      $("handsfree").checked = false;
      setStatus("", "Microphone blocked. Allow mic access, or type below.");
    }
  };
  rec.onend = () => {
    S.listening = false; S.rec = null;
    if (S.interimEl) { S.interimEl.remove(); S.interimEl = null; }
    if (finalText.trim()) send(finalText.trim());
    else if (S.active && !S.busy && !S.speaking && $("handsfree").checked) setTimeout(listen, 250);
  };
  S.rec = rec; S.listening = true;
  setStatus("listening", "Listening… speak now");
  try { rec.start(); } catch (e) { S.listening = false; }
}

// ---------- Call lifecycle ----------
async function startCall() {
  $("transcript").innerHTML = ""; $("summaryCard").style.display = "none"; $("appt").innerHTML = "";
  const r = await api("/api/calls/start", { language: $("language").value, phone: $("phone").value || null });
  S.callId = r.call_id; S.active = true;
  setCallUI(true);
  renderLead(r.lead);
  $("engine").textContent = r.mode;
  bubble("agent", r.greeting, r.returning ? "Returning caller recognised" : "Greeting");
  await speak(r.greeting);
}

async function send(text) {
  if (!S.active || S.busy) return;
  stopListening(); stopSpeaking();
  S.busy = true;
  bubble("user", text);
  setStatus("thinking", "Agent thinking…");
  try {
    const r = await api(`/api/calls/${S.callId}/message`, { text });
    renderTurn(r);
    bubble("agent", r.reply, `${r.intent} · ${r.latency_ms} ms · ${r.mode}`);
    S.busy = false;
    await speak(r.reply);
    if (r.end_call) await endCall();
  } catch (e) {
    S.busy = false;
    setStatus("", "Error: " + e.message);
  }
}

async function endCall() {
  if (!S.callId) return;
  S.active = false; stopListening(); stopSpeaking();
  setCallUI(false);
  setStatus("thinking", "Generating call summary…");
  const r = await api(`/api/calls/${S.callId}/end`, {});
  S.callId = null;
  renderLead(r.lead);
  const list = (arr) => (arr || []).map(x => `<li></li>`).join("");
  $("summary").innerHTML = `
    <p id="sumText" style="font-size:14px;line-height:1.5"></p>
    <dl class="kv"><dt>Outcome</dt><dd id="sumOutcome"></dd><dt>Sentiment</dt><dd id="sumSent"></dd>
    <dt>Priority</dt><dd id="sumPrio"></dd></dl>
    <p class="small" style="margin:10px 0 4px">Key interests</p><ul class="plain" id="sumInt">${list(r.key_interests)}</ul>
    <p class="small" style="margin:10px 0 4px">Objections</p><ul class="plain" id="sumObj">${list(r.objections)}</ul>
    <p class="small" style="margin:10px 0 4px">Next steps</p><ul class="plain" id="sumNext">${list(r.next_steps)}</ul>
    <p style="margin-top:12px"><a href="/dashboard">Open CRM dashboard →</a></p>`;
  $("sumText").textContent = r.summary || "";
  $("sumOutcome").textContent = r.outcome || "-";
  $("sumSent").textContent = r.overall_sentiment || "-";
  $("sumPrio").textContent = r.follow_up_priority || "-";
  [["sumInt", r.key_interests], ["sumObj", r.objections], ["sumNext", r.next_steps]].forEach(([id, arr]) =>
    (arr || []).forEach((x, i) => { $(id).children[i].textContent = x; }));
  $("summaryCard").style.display = "block";
  setStatus("", "Call ended. Summary saved to CRM.");
}

// ---------- boot ----------
(async function init() {
  S.config = await api("/api/config");
  $("company").textContent = S.config.company;
  $("modes").textContent = `LLM: ${S.config.llm_mode} · RAG: ${S.config.rag_mode}`;
  for (const [code, l] of Object.entries(S.config.languages)) {
    const o = document.createElement("option"); o.value = code; o.textContent = l.name; $("language").appendChild(o);
  }
  if (!SR) setStatus("", "Voice input needs Chrome or Edge. You can still type below.");
  renderLead({});
  window.speechSynthesis && speechSynthesis.getVoices();

  $("startBtn").onclick = () => startCall().catch(e => setStatus("", "Error: " + e.message));
  $("endBtn").onclick = () => endCall();
  $("micBtn").onclick = () => { stopSpeaking(); listen(); };
  $("sendBtn").onclick = () => { const t = $("textInput").value.trim(); if (t) { $("textInput").value = ""; send(t); } };
  $("textInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("sendBtn").click(); });
})();
