const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "-").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const KPI_LABELS = {
  total_calls: "Total calls", total_leads: "Leads", hot_leads: "Hot leads", warm_leads: "Warm leads",
  appointments: "Site visits booked", conversion_rate: "Conversion %", avg_lead_score: "Avg lead score",
  avg_call_duration_s: "Avg call (sec)",
};

function sentimentBadge(v) {
  const label = v > 0.2 ? "positive" : v < -0.2 ? "negative" : "neutral";
  return `<span class="badge ${label}">${label} (${(v ?? 0).toFixed(2)})</span>`;
}

async function load() {
  const d = await (await fetch("/api/dashboard")).json();
  $("kpis").innerHTML = Object.entries(KPI_LABELS).map(([k, l]) =>
    `<div class="card kpi"><div class="label">${l}</div><div class="value">${esc(d.kpis[k])}</div></div>`).join("");

  const total = Object.values(d.outcomes).reduce((a, b) => a + b, 0) || 1;
  $("outcomes").innerHTML = Object.entries(d.outcomes).map(([k, v]) =>
    `<div style="margin-bottom:10px"><div class="small">${esc(k)} - ${v}</div><div class="bar"><span style="width:${100 * v / total}%"></span></div></div>`
  ).join("") || `<p class="small">No completed calls yet.</p>`;
  $("objections").innerHTML = d.top_objections.map(([o, n]) => `<li>${esc(o)} <b>×${n}</b></li>`).join("") || `<li class="small">None yet</li>`;
  $("interests").innerHTML = d.top_interests.map(([o, n]) => `<li>${esc(o)} <b>×${n}</b></li>`).join("") || `<li class="small">None yet</li>`;

  $("leads-body").innerHTML = d.leads.map(l => `<tr><td>${l.id}</td><td>${esc(l.name)}</td><td>${esc(l.phone)}</td><td>${esc(l.need)}</td>
    <td>${esc(l.budget)}</td><td>${esc(l.timeline)}</td><td><b>${l.score}</b></td>
    <td><span class="badge ${l.category}">${l.category}</span></td><td>${sentimentBadge(l.sentiment_avg)}</td></tr>`).join("");

  $("calls-body").innerHTML = d.calls.map(c => `<tr class="click" data-id="${c.id}"><td>${c.id}</td><td>${esc(c.lead_name)}</td>
    <td>${esc(c.language)}</td><td>${esc(c.outcome || c.status)}</td><td class="small">${esc(c.summary || "(in progress)")}</td></tr>`).join("");
  document.querySelectorAll("#calls-body tr").forEach(tr => tr.onclick = () => openCall(tr.dataset.id));

  $("appts").innerHTML = d.appointments.map(a => `<tr><td>${esc(a.pretty)}</td><td>${esc(a.lead_name)} ${esc(a.lead_phone)}</td>
    <td>${esc(a.kind)}</td><td><span class="badge positive">${esc(a.status)}</span></td></tr>`).join("") || `<tr><td colspan="4" class="small">No bookings yet</td></tr>`;
}

async function openCall(id) {
  const d = await (await fetch(`/api/calls/${id}`)).json();
  $("mTitle").textContent = `Call #${id} - ${d.lead.name || "Unknown caller"}`;
  const ins = d.call.insights || {};
  $("mBody").innerHTML =
    (d.call.summary ? `<div class="card" style="margin-bottom:10px"><b>AI summary:</b> ${esc(d.call.summary)}<br>
      <span class="small">Next steps: ${esc((ins.next_steps || []).join("; "))}</span></div>` : "") +
    d.messages.map(m => `<div class="bubble ${m.role === "assistant" ? "agent" : "user"}">${esc(m.text)}
      ${m.role === "user" && m.sentiment != null ? `<span class="meta">sentiment ${m.sentiment} · ${esc(m.intent)}</span>` : ""}</div>`).join("");
  $("modal").classList.add("open");
}

load();
setInterval(load, 5000);