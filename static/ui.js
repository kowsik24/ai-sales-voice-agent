// UI layer on top of app.js — no call logic lives here.
// Handles: greeting, live status + timer, pause, field icons, suggestions and live KPI cards.
const USER_NAME = "";   // optional: put the sales rep's name here, e.g. "Rahul"

(function () {
  const $ = (id) => document.getElementById(id);
  const inCall = () => !$("endBtn").disabled;

  // ---- greeting ----
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  $("greeting").textContent = `${part}${USER_NAME ? ", " + USER_NAME : ""} 👋`;
  if (USER_NAME) $("avatar").textContent = USER_NAME.slice(0, 2).toUpperCase();

  // ---- live status, timer, pause availability ----
  let t0 = null, tick = null;
  new MutationObserver(() => {
    const on = inCall();
    $("callCard").classList.toggle("in-call", on);
    $("livePill").classList.toggle("on", on);
    $("liveText").textContent = on ? "Live" : "Idle";
    $("pauseBtn").disabled = !on;
    if (on && !tick) {
      t0 = Date.now(); $("timer").textContent = "00:00";
      tick = setInterval(() => {
        const s = Math.floor((Date.now() - t0) / 1000);
        $("timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
      }, 1000);
    } else if (!on && tick) { clearInterval(tick); tick = null; setPaused(false); refreshKpis(); }
  }).observe($("endBtn"), { attributes: true, attributeFilter: ["disabled"] });

  // voice indicator lights up while listening / speaking
  new MutationObserver(() => {
    const d = $("dot").classList;
    $("micBtn").classList.toggle("live", d.contains("listening") || d.contains("speaking"));
  }).observe($("dot"), { attributes: true, attributeFilter: ["class"] });

  // ---- pause / resume (stops the mic + voice, keeps the call open) ----
  let paused = false;
  function setPaused(p) {
    paused = p;
    $("pauseBtn").classList.toggle("active", p);
    $("pauseBtn").title = p ? "Resume listening" : "Pause listening";
    $("pauseBtn").innerHTML = `<svg class="ic"><use href="#i-${p ? "play" : "pause"}"/></svg>`;
  }
  $("pauseBtn").addEventListener("click", () => {
    if (!inCall()) return;
    if (!paused) {
      try { stopListening(); stopSpeaking(); } catch (e) {}
      $("handsfree").checked = false;
      $("statusText").textContent = "Paused";
      setPaused(true);
    } else {
      $("handsfree").checked = true;
      setPaused(false);
      try { listen(); } catch (e) {}
    }
  });

  // ---- top bar "Live Call" shortcut ----
  $("liveCallBtn").addEventListener("click", () => {
    $("callCard").scrollIntoView({ behavior: "smooth", block: "start" });
    if (!inCall()) $("startBtn").click();
  });

  // ---- icons for lead fields (app.js renders <dt>label</dt><dd>value</dd>) ----
  const ICON = { Name: "user", Phone: "phone", Email: "mail", Requirement: "req", Budget: "wallet",
                 Timeline: "clock", "Decision maker": "check", Location: "pin" };
  new MutationObserver(() => {
    document.querySelectorAll("#leadFields dt:not([data-ic])").forEach(dt => {
      const label = dt.textContent;
      dt.dataset.ic = "1";
      dt.innerHTML = `<svg class="ic"><use href="#i-${ICON[label] || "note"}"/></svg>${label}`;
    });
  }).observe($("leadFields"), { childList: true });

  // ---- suggestion chips ----
  $("suggest").addEventListener("click", (e) => {
    if (e.target.tagName !== "BUTTON") return;
    if ($("textInput").disabled) { $("statusText").textContent = "Start the call first, then tap a suggestion"; return; }
    $("textInput").value = e.target.textContent;
    $("sendBtn").click();
  });

  // ---- KPI cards: real numbers from the CRM ----
  function lakhs(budget) {
    const m = String(budget || "").toLowerCase().match(/(\d+(?:\.\d+)?)\s*(crore|cr|lakh|lac|l\b)/);
    if (!m) return 0;
    return parseFloat(m[1]) * (m[2].startsWith("c") ? 100 : 1);
  }
  function money(l) { return l >= 100 ? `₹${(l / 100).toFixed(2)} Cr` : `₹${Math.round(l)} L`; }
  async function refreshKpis() {
    try {
      const d = await (await fetch("/api/dashboard")).json();
      const k = d.kpis;
      $("k-calls").textContent = k.total_calls;
      $("k-calls-m").textContent = `avg ${k.avg_call_duration_s}s per call`;
      $("k-qual").textContent = k.hot_leads + k.warm_leads;
      $("k-qual-m").textContent = `${k.hot_leads} hot · ${k.warm_leads} warm`;
      $("k-pipe").textContent = money(d.leads.reduce((s, l) => s + lakhs(l.budget), 0));
      $("k-visits").textContent = k.appointments;
      $("k-visits-m").textContent = `conversion ${k.conversion_rate}%`;
    } catch (e) { /* dashboard API not reachable: keep last values */ }
  }
  refreshKpis();
  setInterval(refreshKpis, 10000);
})();