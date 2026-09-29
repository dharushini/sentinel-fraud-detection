/* Sentinel dashboard — friendly front, full functionality underneath. */
(() => {
  const $ = (s) => document.querySelector(s);
  const money = (x) => "₹" + Number(x).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money0 = (x) => "₹" + Math.round(Number(x)).toLocaleString("en-IN");
  const pct = (x, d) => (x * 100).toFixed(d ?? (x >= 0.1 ? 0 : 1)) + "%";

  const I = {
    pos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/></svg>',
    online: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/></svg>',
    atm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M9 17h6"/></svg>',
    transfer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9h13l-3-3M20 15H7l3 3"/></svg>',
    ALLOW: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 13l4 4L19 7"/></svg>',
    REVIEW: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="3"/><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/></svg>',
    CHALLENGE: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3"/></svg>',
    BLOCK: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/></svg>',
    brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4a3 3 0 0 0-1-1zM15 3a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/></svg>',
    up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M7 14l5-5 5 5"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M7 10l5 5 5-5"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z"/></svg>',
    money: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M12 7v10M9.5 9.5a2.5 2 0 0 1 5 0c0 2-5 1-5 3a2.5 2 0 0 0 5 0"/></svg>',
    pulse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>',
  };
  const chanIco = (c) => I[c] || I.online;

  const VERDICT = {
    ALLOW: { label: "Allowed", verb: "was allowed through" },
    REVIEW: { label: "Flagged", verb: "was allowed but flagged for an analyst" },
    CHALLENGE: { label: "Verification needed", verb: "was paused for identity verification" },
    BLOCK: { label: "Blocked", verb: "was blocked" },
  };

  const PRETTY = {
    amount_z: "amount vs personal norm", amount_to_max: "amount vs personal record",
    speed_kmh: "travel speed", impossible_travel: "impossible travel",
    new_country: "new country", new_device: "new device", new_merchant: "new merchant",
    new_beneficiary: "new payee", failed_logins_1h: "failed logins (1h)",
    txn_count_1h: "txns last hour", txn_count_5m: "txns last 5 min",
    amt_sum_1h: "spent last hour", distinct_merchants_24h: "distinct merchants 24h",
    high_risk_mcc: "high-risk category", is_night: "overnight",
    merchant_fraud_rate: "merchant fraud history", device_fraud_rate: "device fraud history",
    bin_fraud_rate: "card-BIN fraud history", beneficiary_fraud_rate: "payee fraud history",
    entity_max_fraud_rate: "worst entity fraud history", ring_size: "fraud-ring size",
    device_customer_fanout: "accounts on this device", beneficiary_customer_fanin: "accounts paying this payee",
    seq_surprise: "unusual for this customer", seq_new_token: "never-seen behaviour",
    seq_repeat_5: "rapid repetition", seq_regime_kl: "sudden behaviour shift",
  };
  const nice = (k) => PRETTY[k] || k.replace(/_/g, " ");
  const FEATURE_DESC = {
    amount_z: "Standard deviations from this customer's mean spend.",
    new_device: "1 = came from an unrecognised device.",
    new_country: "1 = first transaction ever in this country.",
    new_beneficiary: "1 = this payee has never been paid before.",
    impossible_travel: "1 = distance from the last transaction can't be covered in the elapsed time.",
    seq_regime_kl: "How far the recent behaviour mix has moved from the customer's long-run pattern.",
    seq_surprise: "How unlikely this behaviour is given the customer's history (0–1).",
    merchant_fraud_rate: "Time-decayed historical fraud rate for this merchant.",
    beneficiary_fraud_rate: "Historical fraud rate for this payee account.",
    ring_size: "Distinct customers sharing this device or payee (fraud-ring signal).",
    entity_max_fraud_rate: "Worst of the merchant / BIN / device / payee fraud rates.",
    txn_count_5m: "Transactions by this customer in the last 5 minutes.",
    failed_logins_1h: "Failed login attempts in the last hour.",
  };
  const ENTITY_KEYS = new Set(["merchant_fraud_rate","merchant_txn_count","merchant_age_days","bin_fraud_rate",
    "device_fraud_rate","device_customer_fanout","device_is_global_new","beneficiary_fraud_rate",
    "beneficiary_customer_fanin","beneficiary_is_global_new","beneficiary_txn_count","entity_max_fraud_rate","ring_size"]);

  /* ---------------- mode toggle ---------------- */
  let mode = localStorage.getItem("sentinel_mode") || "simple";
  function applyMode() {
    document.body.classList.toggle("analyst", mode === "analyst");
    document.querySelectorAll("#modeSeg button").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === mode));
  }
  $("#modeSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    mode = b.dataset.mode; localStorage.setItem("sentinel_mode", mode); applyMode();
    if (selected != null) selectCase(selected);
  });
  applyMode();
  $("#introX").addEventListener("click", () => $("#intro").remove());

  /* ---------------- charts ---------------- */
  const riskBuckets = new Array(10).fill(0); let bucketWin = [];
  const decChart = new Chart($("#decChart"), {
    type: "bar", data: { labels: [], datasets: [
      { label: "Allowed", data: [], backgroundColor: "#34d399" },
      { label: "Flagged", data: [], backgroundColor: "#fbbf24" },
      { label: "Verify", data: [], backgroundColor: "#fb923c" },
      { label: "Blocked", data: [], backgroundColor: "#f87171" }] },
    options: { responsive: true, animation: false,
      scales: { x: { stacked: true, ticks: { color: "#93a0b4" }, grid: { display: false } },
        y: { stacked: true, ticks: { color: "#93a0b4" }, grid: { color: "#262e3d" } } },
      plugins: { legend: { labels: { color: "#93a0b4", boxWidth: 10 } } } },
  });
  const riskChart = new Chart($("#riskChart"), {
    type: "bar", data: { labels: ["0","10","20","30","40","50","60","70","80","90"].map((x)=>x+"%"),
      datasets: [{ data: riskBuckets, backgroundColor: ["#34d399","#34d399","#5ec98f","#8fce7f","#fbbf24","#fbbf24","#fb923c","#f0743e","#f87171","#f87171"] }] },
    options: { responsive: true, animation: false,
      scales: { x: { ticks: { color: "#93a0b4" }, grid: { display: false } },
        y: { ticks: { color: "#93a0b4" }, grid: { color: "#262e3d" } } },
      plugins: { legend: { display: false } } },
  });
  let dbin = { ALLOW: 0, REVIEW: 0, CHALLENGE: 0, BLOCK: 0 }, dcount = 0, didx = 0;
  function tickDec(a) {
    dbin[a]++; dcount++;
    if (dcount >= 15) {
      const d = decChart.data; d.labels.push(String(++didx));
      d.datasets[0].data.push(dbin.ALLOW); d.datasets[1].data.push(dbin.REVIEW);
      d.datasets[2].data.push(dbin.CHALLENGE); d.datasets[3].data.push(dbin.BLOCK);
      if (d.labels.length > 24) { d.datasets.forEach((s) => s.data.shift()); d.labels.shift(); }
      decChart.update(); dbin = { ALLOW: 0, REVIEW: 0, CHALLENGE: 0, BLOCK: 0 }; dcount = 0;
    }
  }

  /* ---------------- hero stats ---------------- */
  function healthLabel(s) {
    return { warming: "Calibrating", stable: "Healthy", watch: "Watch", alert: "Drifting" }[s] || "—";
  }
  function renderHero(m, drift) {
    m = m || {};
    m.processed = m.processed || 0; m.fraud_total = m.fraud_total || 0;
    m.fraud_stopped = m.fraud_stopped || 0; m.false_positives = m.false_positives || 0;
    const legit = Math.max(m.processed - m.fraud_total, 1);
    const d = drift || m.drift || { state: "warming", psi: 0 };
    $("#hero").innerHTML = `
      <div class="stat good">
        <div class="k">${I.shield} Fraud caught</div>
        <div class="v">${pct(m.detection_rate || 0)}</div>
        <div class="s">${m.fraud_stopped}/${m.fraud_total} stopped before the money moved</div>
      </div>
      <div class="stat">
        <div class="k">${I.ALLOW} Good customers stopped</div>
        <div class="v">${pct(m.false_positive_rate || 0, 2)}</div>
        <div class="s">${m.false_positives} of ${legit.toLocaleString()} legit — lower is better</div>
      </div>
      <div class="stat good">
        <div class="k">${I.money} Money protected</div>
        <div class="v">${money0(m.amount_saved || 0)}</div>
        <div class="s">value of blocked / challenged fraud</div>
      </div>
      <div class="stat">
        <div class="k">${I.pulse} Model health</div>
        <div class="health ${d.state}"><span class="dot"></span>${healthLabel(d.state)}</div>
        <div class="s analyst-only">drift PSI ${(+d.psi).toFixed(3)}${d.psi_raw != null ? ` (raw ${(+d.psi_raw).toFixed(2)})` : ""} · ${m.processed.toLocaleString()} scored</div>
        <div class="s" style="${mode==='analyst'?'display:none':''}">${m.processed.toLocaleString()} transactions scored</div>
      </div>`;
  }

  /* ---------------- policy A/B ---------------- */
  function renderPolicy(pc) {
    if (!pc) return;
    const defs = [
      ["rules_only", "Rules only", "hand-written tripwires"],
      ["model_only", "AI only", "the machine-learning score"],
      ["full", "Sentinel", "rules + AI together"],
    ];
    const max = Math.max(...defs.map(([k]) => (pc[k] || {}).detection_rate || 0), 0.01);
    $("#pbars").innerHTML = defs.map(([k, nm, sub]) => {
      const v = pc[k] || { detection_rate: 0, false_positive_rate: 0 };
      return `<div class="pbar ${k === "full" ? "win" : ""}">
        <div class="nm">${nm}<br><span style="color:var(--faint);font-size:11px">${sub}</span></div>
        <div class="track"><div class="fill" style="width:${(v.detection_rate / max) * 100}%"></div></div>
        <div class="n">${pct(v.detection_rate)}<br><small>${pct(v.false_positive_rate, 2)} FP</small></div>
      </div>`;
    }).join("");
    const f = pc.full?.detection_rate || 0, best = Math.max(pc.rules_only?.detection_rate || 0, pc.model_only?.detection_rate || 0);
    if (f > 0 && best > 0) {
      const x = (f / best).toFixed(1);
      $("#policyLead").textContent = `Together they catch ${pct(f)} of fraud — about ${x}× what either approach manages alone, at the same false-positive rate.`;
    }
  }

  /* ---------------- feed ---------------- */
  const feed = $("#feed"), MAX = 55, cache = new Map();
  let selected = null;
  function addTxn(c) {
    cache.set(c.id, c);
    const el = document.createElement("div");
    el.className = "txn"; el.dataset.id = c.id;
    const v = VERDICT[c.action];
    const flags = [];
    if (c.features?.new_device >= 1) flags.push("new device");
    else if (c.features?.new_country >= 1) flags.push("new country");
    else if (c.features?.new_beneficiary >= 1) flags.push("new payee");
    const place = c.city || c.country || "";
    const noun = c.channel === "transfer" ? "transfer" : c.channel === "atm" ? "ATM withdrawal" : `${c.mcc.replace(/_/g," ")} · ${c.channel}`;
    const ageChip = c.age_bracket ? `<span class="agechip age-${c.age_bracket.replace("+","p")}" title="Customer age ${c.cust_age}">${c.age_bracket}</span>` : "";
    el.innerHTML = `
      <div class="ico">${chanIco(c.channel)}</div>
      <div class="main">
        <div class="amt">${money(c.amount)}</div>
        <div class="sub">${ageChip}${noun}${place ? " · " + place : ""}${flags.length ? ' · <span class="flag">' + flags[0] + "</span>" : ""}</div>
      </div>
      <div class="right">
        <span class="verdict v-${c.action}">${I[c.action]}${v.label}</span>
        <span class="risknum analyst-only">${Math.round(c.risk * 100)}%</span>
      </div>`;
    el.addEventListener("click", () => selectCase(c.id, el));
    feed.prepend(el);
    while (feed.children.length > MAX) feed.removeChild(feed.lastChild);
    const b = Math.min(9, Math.floor(c.risk * 10));
    riskBuckets[b]++; bucketWin.push(b);
    if (bucketWin.length > 350) riskBuckets[bucketWin.shift()]--;
    riskChart.update();
    tickDec(c.action);
  }

  /* ---------------- case detail ---------------- */
  function aiSummary(s) {
    if (!s) return "";
    const li = (arr, cls) => (arr || []).map((x) => `<li class="${cls}">${x}</li>`).join("");
    return `<div class="aisum">
      <div class="aihead">${I.brain} AI summary — ${s.headline}</div>
      <p>${s.summary}</p>
      ${(s.drivers?.length || s.mitigators?.length) ? `<ul class="aifac">${li(s.drivers,"up")}${li(s.mitigators,"down")}</ul>` : ""}
      <div class="aireco"><b>Recommended:</b> ${s.recommendation}</div>
      ${s.ground_truth ? `<div class="aigt">${s.ground_truth}</div>` : ""}
    </div>`;
  }
  function contribBars(expl) {
    if (!expl?.length) return "";
    const max = Math.max(...expl.map((e) => Math.abs(e.contribution)), 1e-6);
    return `<div class="contribs">` + expl.map((e) => {
      const w = (Math.abs(e.contribution) / max) * 50, cls = e.contribution >= 0 ? "pos" : "neg";
      return `<div class="c"><span class="lbl" title="${FEATURE_DESC[e.feature] || e.feature}">${nice(e.feature)}</span>
        <span class="track"><i class="${cls}" style="width:${w}%"></i></span>
        <span class="val">${e.contribution >= 0 ? "+" : ""}${(e.contribution * 100).toFixed(0)} pp</span></div>`;
    }).join("") + `</div>`;
  }
  function counterfactualCard(cf) {
    if (!cf) return "";
    if (!cf.found) return `<div class="cfbox"><b>Counterfactual search</b><div>${cf.message || "No reference path found."}</div><small>${cf.note || ""}</small></div>`;
    const groups = (cf.groups || []).map((g) => `<li><b>${g.label}</b>: ${g.changes.map((x) => `${nice(x.feature)} ${x.from} → ${x.to}`).join(", ")}</li>`).join("");
    return `<div class="cfbox"><b>Grouped counterfactual path</b>
      <div>Calibrated fraud probability ${pct(cf.base_probability)} → ${pct(cf.counterfactual_probability)}
      (model threshold ${pct(cf.target_probability)}).</div>
      <ul>${groups}</ul><small>${cf.note || "Hypothetical comparison, not causal advice."}</small></div>`;
  }
  function featGroups(features) {
    return [["Behavioural", (k) => !k.startsWith("seq_") && !ENTITY_KEYS.has(k)],
            ["Sequence model", (k) => k.startsWith("seq_")],
            ["Entity & graph", (k) => ENTITY_KEYS.has(k)]].map(([nm, t]) => {
      const rows = Object.entries(features).filter(([k]) => t(k));
      if (!rows.length) return "";
      return `<details class="block"><summary>${nm} <span style="color:var(--faint)">(${rows.length})</span></summary>
        <div class="inner feat-grid">${rows.map(([k, v]) => `<div title="${FEATURE_DESC[k] || ""}"><span>${k}</span><b>${v}</b></div>`).join("")}</div></details>`;
    }).join("");
  }
  const WHATIF = [["new_device","unrecognised device"],["new_country","new country"],["new_beneficiary","new payee"],
    ["is_night","overnight"],["high_risk_mcc","high-risk category"],["impossible_travel","impossible travel"]];

  function selectCase(id, row) {
    document.querySelectorAll(".txn.sel").forEach((r) => r.classList.remove("sel"));
    if (row) row.classList.add("sel");
    const c = cache.get(id); if (!c) return;
    selected = id;
    const v = VERDICT[c.action];
    const alert = c.customer_alert
      ? `<div class="alertbox"><div class="l">Message sent to customer</div>${c.customer_alert}</div>` : "";
    const VER = {
      pending: `<div class="verif pending"><div class="l">Verification (OTP) result</div>
        <div class="btns"><button data-ver="1">Customer passed</button><button data-ver="0">Verification failed</button></div>
        <div class="hint">Passed = the payment goes through and becomes part of this customer's normal behaviour. Failed = recorded as confirmed fraud.</div></div>`,
      passed: `<div class="verif ok">Customer passed verification — payment went through.</div>`,
      failed: `<div class="verif bad">Verification failed — recorded as confirmed fraud.</div>`,
      simulated_pass: `<div class="verif ok">Verification: the genuine customer passed (simulated from the known outcome).</div>`,
      simulated_fail: `<div class="verif bad">Verification: failed — the fraudster couldn't complete it (simulated from the known outcome).</div>`,
    };
    const verif = c.verification ? (VER[c.verification] || "") : "";
    const sh = c.shadows || {};
    const shadow = (sh.rules_only && (sh.rules_only !== c.action || sh.model_only !== c.action))
      ? `<div class="shadow">Rules alone would <b>${VERDICT[sh.rules_only].label.toLowerCase()}</b> ·
         AI alone would <b>${VERDICT[sh.model_only].label.toLowerCase()}</b> · together: <b>${v.label.toLowerCase()}</b></div>` : "";
    const chips = WHATIF.map(([k, l]) => `<button class="wchip ${(c.features[k]||0)>=1?"on":""}" data-k="${k}">${l}</button>`).join("");

    $("#detail").innerHTML = `
      <div class="vhero ${c.action}">
        <div class="badge">${I[c.action]}</div>
        <div>
          <h2>${v.label}</h2>
          <div class="line">${money(c.amount)} ${c.channel === "transfer" ? "transfer" : c.mcc.replace(/_/g," ") + " " + c.channel}
            · ${c.cust_id}${c.age_bracket ? ` · age ${c.cust_age} (${c.age_bracket})` : ""} · ${c.city || ""} ${c.country} · ${new Date(c.ts).toLocaleTimeString()}</div>
        </div>
      </div>
      ${aiSummary(c.summary)}
      ${alert}
      ${verif}
      <div class="ask">
        <div class="q">Was this the right call?</div>
        <div class="btns">
          <button class="yes" data-fb="1">${I.up} It was fraud</button>
          <button class="no" data-fb="0">${I.down} It was fine</button>
        </div>
      </div>
      <details class="block whatif"><summary>Try changing the transaction</summary>
        <div class="inner">
          <div class="wrow">amount ×<input type="range" id="wmult" min="0.1" max="5" step="0.1" value="1"><b id="wmultv">1.0×</b></div>
          <div class="wchips">${chips}</div>
          <div class="wout" id="wout">move a control to see how the decision changes</div>
        </div>
      </details>
      <div class="analyst-only">
        ${shadow}
        <details class="block" id="graphBlock"><summary>🔎 Entity graph — real connections</summary>
          <div class="inner">
            <div class="hint" style="margin-bottom:8px">Devices, payees, or merchants this transaction shares with OTHER customers Sentinel has scored — built from the same fraud-ring signal behind <code>ring_size</code> and the fan-in/fan-out features. No synthetic decoration: every node and edge is a real transaction.</div>
            <div id="graphCanvas" style="width:100%;height:320px;border:1px solid var(--line);border-radius:10px;background:var(--panel-2)"></div>
            <div class="hint" id="graphEmpty" style="margin-top:8px;display:none">No shared devices, payees, or merchants found for this transaction — it looks isolated, which is itself a signal (most fraud rings share something).</div>
          </div>
        </details>
        <div class="scores">
          <div class="scorebox"><div class="l">Risk</div><div class="v">${pct(c.risk)}</div></div>
          <div class="scorebox"><div class="l">Fraud proba</div><div class="v">${pct(c.fraud_proba)}</div></div>
          <div class="scorebox"><div class="l">Novelty</div><div class="v">${pct(c.anomaly)}</div></div>
        </div>
        <details class="block"><summary>Why — signal breakdown</summary><div class="inner">
          <ul class="reasons">${c.reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
          ${counterfactualCard(c.counterfactual)}
          <div class="cfcaption">Single-feature probes vs training median (Δ calibrated probability; not additive)</div>
          ${contribBars(c.explanation)}
        </div></details>
        ${featGroups(c.features)}
      </div>`;

    $("#detail").querySelectorAll("[data-fb]").forEach((b) =>
      b.addEventListener("click", () => sendFeedback(c, +b.dataset.fb)));
    $("#detail").querySelectorAll("[data-ver]").forEach((b) =>
      b.addEventListener("click", async () => {
        const passed = b.dataset.ver === "1";
        try {
          const r = await fetch(`/cases/${c.id}/verification`, { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passed }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) { toast(typeof j.detail === "string" ? j.detail : "Couldn't record the verification."); return; }
          c.verification = j.verification; cache.set(c.id, c); selectCase(c.id);
          toast(passed ? "Recorded: customer verified — learned as normal behaviour." : "Recorded: verification failed — marked as confirmed fraud.");
        } catch { toast("Couldn't reach the verification API."); }
      }));
    wireWhatIf(c);
    loadGraph(c.id);
  }

  /* ---------------- entity graph (forensics) ---------------- */
  const NODE_COLOR = {
    focus_customer: "#60a5fa", linked_customer: "#93a0b4",
    entity_device: "#fb923c", entity_beneficiary: "#f87171", entity_merchant: "#fbbf24",
  };
  let cy = null;
  async function loadGraph(caseId) {
    const empty = $("#graphEmpty"), canvas = $("#graphCanvas");
    if (!canvas) return;
    if (cy) { cy.destroy(); cy = null; }
    let g;
    try {
      const r = await fetch(`/graph/${caseId}`);
      if (!r.ok) { canvas.style.display = "none"; empty.style.display = "block"; empty.textContent = "Couldn't load the graph for this transaction."; return; }
      g = await r.json();
    } catch { return; }
    if (!g.edges.length) {
      canvas.style.display = "none"; empty.style.display = "block";
      empty.textContent = "No shared devices, payees, or merchants found for this transaction — it looks isolated, which is itself a signal (most fraud rings share something).";
      return;
    }
    canvas.style.display = "block"; empty.style.display = "none";
    const elements = [
      ...g.nodes.map((n) => ({ data: { id: n.id, label: n.kind.startsWith("entity_") ? `${n.kind.replace("entity_","")}\n${n.label}` : n.label, kind: n.kind, fraudRate: n.fraud_rate } })),
      ...g.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, kind: e.kind } })),
    ];
    cy = cytoscape({
      container: canvas,
      elements,
      style: [
        { selector: "node", style: {
          "background-color": (n) => NODE_COLOR[n.data("kind")] || "#93a0b4",
          "label": "data(label)", "color": "#e6ebf5", "font-size": 10, "text-wrap": "wrap",
          "text-valign": "bottom", "text-margin-y": 6, "width": (n) => n.data("kind") === "focus_customer" ? 34 : 24,
          "height": (n) => n.data("kind") === "focus_customer" ? 34 : 24,
          "border-width": (n) => n.data("kind") === "focus_customer" ? 3 : 0,
          "border-color": "#fff",
        } },
        { selector: "edge", style: {
          "width": 1.5, "line-color": "#3a4356", "curve-style": "bezier",
          "target-arrow-shape": "none",
        } },
      ],
      layout: { name: "cose", animate: false, padding: 20 },
      userZoomingEnabled: true, userPanningEnabled: true, boxSelectionEnabled: false,
    });
    cy.on("tap", "node", (evt) => {
      const d = evt.target.data();
      if (d.fraudRate != null) toast(`<b>${d.label}</b> — ${d.fraudRate >= 0 ? pct(d.fraudRate, 1) : "?"} historical fraud rate on this entity.`);
    });
  }

  $("#ringBtn")?.addEventListener("click", async (e) => {
    const btn = e.target;
    btn.disabled = true; btn.textContent = "Injecting ring…";
    try {
      const j = await (await fetch(`/simulator/inject_ring/${btn.dataset.s}?ring_size=4`, { method: "POST" })).json();
      toast(`Injected a fraud ring: ${j.ring_size} customers sharing one device/mule account. Click a flagged transaction and open "Entity graph" to see the real connections.`);
    } catch { toast("Couldn't inject the ring."); }
    finally { btn.disabled = false; btn.textContent = "Inject a fraud ring (4 customers)"; }
  });

  function wireWhatIf(c) {
    const st = { amount_mult: 1 }, m = $("#wmult"), out = $("#wout");
    let t = null;
    const run = () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        let j;
        try {
          const r = await fetch("/whatif", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ case_id: c.id, overrides: st }) });
          if (!r.ok) { out.textContent = "this transaction has scrolled out of memory"; return; }
          j = await r.json();
        } catch { out.textContent = "couldn't reach the scorer"; return; }
        const w = j.whatif;
        out.innerHTML = `<span class="verdict v-${w.action}">${I[w.action]}${VERDICT[w.action].label}</span>
          risk <b>${pct(w.risk)}</b>${j.flipped ? `<span class="wflip">— changed from ${VERDICT[j.base.action].label}</span>` : ""}
          <div class="wsum">${j.summary.summary}</div>`;
      }, 200);
    };
    m.addEventListener("input", () => { st.amount_mult = +m.value; $("#wmultv").textContent = (+m.value).toFixed(1) + "×"; run(); });
    $("#detail").querySelectorAll(".wchip").forEach((b) =>
      b.addEventListener("click", () => { b.classList.toggle("on"); st[b.dataset.k] = b.classList.contains("on") ? 1 : 0; run(); }));
  }

  async function sendFeedback(c, label) {
    const r = await fetch("/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cust_id: c.cust_id, ts: c.ts, amount: c.amount, label,
        kind: label ? "chargeback" : "disposition", note: c.merchant_id }) });
    const j = await r.json().catch(() => ({}));
    const online = j.recorded?.online_status;
    let msg = `Recorded: ${c.cust_id} ${money(c.amount)} → ${label ? "fraud" : "legitimate"}. The payee/device risk updated and the next retrain will use it.`;
    if (online) {
      msg += online.active
        ? ` Online correction layer: ${online.updates} labels seen, now actively adjusting similar transactions (±${pct(online.max_adjustment,0)} max).`
        : ` Online correction layer: ${online.updates}/5 labels seen — needs a few more before it starts adjusting.`;
    }
    toast(msg);
  }

  /* ---------------- live data: websocket + polling fallback ---------------- */
  let ws = null, polling = false, pollTimer = null, simulatorOn = true;
  function onMsg(msg) {
    if (msg.type === "snapshot") {
      renderHero(msg.metrics, msg.drift); renderPolicy(msg.metrics.policy_comparison);
      (msg.cases || []).slice().reverse().forEach(addTxn);
    } else if (msg.type === "case") {
      addTxn(msg.case);
      $("#feedcount").textContent = (+$("#feedcount").textContent + 1);
      if (selected === msg.case.id) selectCase(msg.case.id);
    } else if (msg.type === "metrics") {
      renderHero(msg.metrics, msg.drift); renderPolicy(msg.metrics.policy_comparison);
    }
  }
  function setLive(on, txt) { $("#pulse").classList.toggle("on", on); $("#livetxt").textContent = txt; }

  function startPolling() {
    if (polling) return;
    polling = true; setLive(true, "live (polling)");
    const loop = async () => {
      if (!paused) {
        try {
          if (simulatorOn) {
            const r = await fetch("/tick?n=" + Math.max(1, Math.round(rate * 2.5)), { method: "POST" });
            const j = await r.json();
            (j.cases || []).forEach((cs) => onMsg({ type: "case", case: cs }));
            onMsg({ type: "metrics", metrics: j.metrics, drift: j.drift });
          } else {
            // no synthetic traffic on this deployment: just pick up real
            // transactions scored since the last poll
            const cs = await (await fetch("/cases?limit=60")).json();
            cs.slice().reverse().filter((c) => !cache.has(c.id)).forEach((c) => onMsg({ type: "case", case: c }));
            const m = await (await fetch("/metrics")).json();
            onMsg({ type: "metrics", metrics: m });
          }
          setLive(true, simulatorOn ? "live (polling)" : "live");
        } catch { setLive(false, "reconnecting…"); }
      }
      pollTimer = setTimeout(loop, 2500);
    };
    fetch("/cases?limit=40").then((r) => r.json()).then((cs) =>
      cs.slice().reverse().forEach(addTxn)).catch(() => {});
    fetch("/metrics").then((r) => r.json()).then((m) => {
      renderHero(m); renderPolicy(m.policy_comparison);
    }).catch(() => {});
    loop();
  }
  function connect() {
    try {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/stream`);
    } catch { startPolling(); return; }
    ws.onopen = () => setLive(true, "live");
    ws.onmessage = (e) => onMsg(JSON.parse(e.data));
    ws.onclose = () => { setLive(false, "reconnecting…"); if (!polling) setTimeout(tryReconnect, 1500); };
    ws.onerror = () => { try { ws.close(); } catch {} };
    setInterval(() => { if (ws && ws.readyState === 1) ws.send("ping"); }, 15000);
  }
  let wsFails = 0;
  function tryReconnect() { if (++wsFails >= 2) startPolling(); else connect(); }

  /* ---------------- controls ---------------- */
  document.querySelectorAll(".abtn[data-s]").forEach((b) =>
    b.addEventListener("click", async () => {
      const j = await (await fetch(`/simulator/inject/${b.dataset.s}`, { method: "POST" })).json();
      toast(`Injected a <b>${b.textContent.toLowerCase()}</b> attack against ${j.cust_id} in ${j.victim_city}. Watch the feed.`);
      if (j.cases) j.cases.forEach((cs) => onMsg({ type: "case", case: cs }));
    }));
  let rate = 2;
  $("#rate").addEventListener("input", (e) => {
    rate = +e.target.value; $("#ratev").textContent = rate + "/s";
    fetch("/simulator/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rate }) });
  });
  let paused = false;
  $("#pauseBtn").addEventListener("click", () => {
    paused = !paused; $("#pauseBtn").textContent = paused ? "Resume" : "Pause";
    fetch("/simulator/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ running: !paused }) });
  });
  function toast(html) {
    const t = document.createElement("div"); t.className = "toast"; t.innerHTML = html;
    document.body.appendChild(t); setTimeout(() => t.remove(), 4600);
  }

  /* ---------------- manual transaction entry ---------------- */
  $("#manualForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const custRaw = $("#mCust").value.trim();
    const body = {
      cust_id: custRaw || ("manual_" + Math.random().toString(36).slice(2, 8)),
      amount: parseFloat($("#mAmount").value),
      merchant_id: $("#mMerchant").value.trim() || "merchant_manual",
      mcc: $("#mMcc").value,
      channel: $("#mChannel").value,
      city: $("#mCity").value.trim(),
      country: ($("#mCountry").value.trim() || "IN").toUpperCase(),
      device_id: $("#mDevice").value.trim() || ("dev_manual_" + Math.random().toString(36).slice(2, 6)),
      label: 0,
    };
    // Only send an age if one was actually typed — omitting the key lets the
    // backend apply its deterministic per-customer fallback instead of us
    // inventing a number here.
    const ageRaw = $("#mAge")?.value.trim();
    if (ageRaw) body.cust_age = parseInt(ageRaw, 10);
    if (!body.amount || body.amount <= 0) { toast("Enter a valid amount first."); return; }
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Scoring…";
    try {
      const r = await fetch("/score", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) { const err = await r.json().catch(() => ({})); toast(`Couldn't score that: ${err.detail || r.statusText}`); return; }
      const case_ = await r.json();
      onMsg({ type: "case", case: case_ });
      $("#feedcount").textContent = (+$("#feedcount").textContent + 1);
      selectCase(case_.id);
      toast(`Scored — <b>${VERDICT[case_.action].label}</b>. Click it in the feed for the full explanation.`);
    } catch { toast("Couldn't reach the scoring API."); }
    finally { btn.disabled = false; btn.textContent = "Score this transaction"; }
  });

  /* ---------------- bulk CSV upload ---------------- */
  let bulkText = null, bulkResults = [];
  const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  $("#bulkFile")?.addEventListener("change", (e) => {
    const f = e.target.files[0];
    bulkText = null; $("#bulkOut").innerHTML = "";
    $("#bulkCheck").disabled = $("#bulkScore").disabled = true;
    if (!f) { $("#bulkName").textContent = "Choose a CSV file…"; return; }
    $("#bulkName").textContent = `${f.name} (${(f.size / 1024).toFixed(0)} KB)`;
    if (f.size > 1_000_000) { $("#bulkOut").innerHTML = `<div class="uerr">That file is over 1 MB — split it into smaller files.</div>`; return; }
    const rd = new FileReader();
    rd.onload = () => { bulkText = rd.result; $("#bulkCheck").disabled = $("#bulkScore").disabled = false; };
    rd.onerror = () => { $("#bulkOut").innerHTML = `<div class="uerr">Couldn't read that file.</div>`; };
    rd.readAsText(f);
  });

  function renderBulk(j, scored) {
    const errs = (j.errors || []).map((e) => `<tr><td>row ${e.row}</td><td>${esc(e.error)}</td></tr>`).join("");
    const warn = (j.warnings || []).map((w) => `<div class="uwarn">${esc(w)}</div>`).join("");
    let html = `<div class="usum">
      <b>${j.rows.toLocaleString("en-IN")}</b> rows · <b class="ok">${j.valid.toLocaleString("en-IN")}</b> valid ·
      <b class="${j.rejected ? "bad" : ""}">${j.rejected}</b> rejected${scored ? ` · <b>${j.scored}</b> scored` : " — nothing scored yet"}</div>${warn}`;
    if (scored && j.decision_mix) {
      const d = j.decision_mix;
      html += `<div class="umix">${["ALLOW", "REVIEW", "CHALLENGE", "BLOCK"].map((a) =>
        `<span class="verdict v-${a}">${I[a]}${VERDICT[a].label}: ${d[a]}</span>`).join(" ")}</div>`;
    }
    if (scored && j.labelled) {
      const L = j.labelled, cm = L.confusion_matrix;
      const bcm = L.block_confusion_matrix || cm;
      html += `<div class="ulab">On the ${L.labelled_rows} labelled rows: caught <b>${cm.tp}</b> of ${cm.tp + cm.fn} fraud
        (${nicePct(L.recall)} stopped recall), <b>${bcm.fp}</b> genuine transaction${bcm.fp === 1 ? "" : "s"} actually blocked
        (${nicePct(L.false_positive_rate, 2)} FP rate). Stopped-friction FP rate including challenges: ${nicePct(L.stopped_false_positive_rate ?? L.false_positive_rate, 2)}.
        Precision of stopped alerts: ${nicePct(L.precision)}.</div>`;
    }
    if (errs) html += `<details class="uerrs" ${scored ? "" : "open"}><summary>${j.rejected} rejected row${j.rejected === 1 ? "" : "s"} — why</summary>
      <table>${errs}</table>${j.rejected > (j.errors || []).length ? `<div class="hint">Showing the first ${(j.errors || []).length}.</div>` : ""}</details>`;
    if (scored && j.results?.length) {
      bulkResults = j.results;
      const flagged = j.results.filter((r) => r.action !== "ALLOW").slice(0, 25);
      html += `<div class="ures"><div class="urhd"><b>${flagged.length ? "Flagged transactions" : "No transactions flagged"}</b>
          <button class="ghost" id="bulkDl">Download all results (CSV)</button></div>
        ${flagged.length ? `<table>${flagged.map((r) => `<tr data-id="${r.id}">
          <td>${esc(r.cust_id)}</td><td>${money(r.amount)}</td>
          <td><span class="verdict v-${r.action}">${VERDICT[r.action].label}</span></td>
          <td class="ureason">${esc(r.reason)}</td></tr>`).join("")}</table>
          <div class="hint">Click a row to open its full explanation.</div>` : ""}</div>`;
    }
    $("#bulkOut").innerHTML = html;
    $("#bulkDl")?.addEventListener("click", () => {
      const cols = ["id", "ts", "cust_id", "amount", "action", "risk", "age_bracket", "label", "reason"];
      const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const csvOut = [cols.join(","), ...bulkResults.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csvOut], { type: "text/csv" }));
      a.download = "sentinel_results.csv"; a.click(); URL.revokeObjectURL(a.href);
    });
    document.querySelectorAll(".ures tr[data-id]").forEach((tr) =>
      tr.addEventListener("click", async () => {
        const id = +tr.dataset.id;
        if (!cache.has(id)) {
          try {
            const r = await fetch(`/cases/${id}`);
            if (!r.ok) { toast("That case has aged out of the live buffer."); return; }
            cache.set(id, await r.json());
          } catch { toast("Couldn't load that case."); return; }
        }
        selectCase(id);
        $("#detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }));
  }

  async function sendBulk(validateOnly) {
    if (!bulkText) return;
    const btn = validateOnly ? $("#bulkCheck") : $("#bulkScore"), original = btn.textContent;
    $("#bulkCheck").disabled = $("#bulkScore").disabled = true;
    btn.textContent = validateOnly ? "Checking…" : "Scoring…";
    try {
      const r = await fetch("/upload/transactions", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv: bulkText, validate_only: validateOnly }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        $("#bulkOut").innerHTML = `<div class="uerr">${esc(typeof j.detail === "string" ? j.detail : "The file couldn't be processed.")}</div>`;
        return;
      }
      renderBulk(j, !validateOnly);
      if (!validateOnly) {
        // With a live WebSocket every scored case already arrived via
        // broadcast. Without one (serverless/polling) pull them in, so the
        // feed shows the upload either way — and nothing is counted twice.
        if (polling || !ws || ws.readyState !== 1) {
          try {
            const cs = await (await fetch(`/cases?limit=${Math.min(j.scored, 60)}`)).json();
            cs.slice().reverse().filter((c) => !cache.has(c.id)).forEach((c) => {
              addTxn(c); $("#feedcount").textContent = (+$("#feedcount").textContent + 1);
            });
          } catch {}
        }
        toast(`Scored ${j.scored} uploaded transactions — ${j.stopped} stopped.`);
        loadAgeBreakdown();
      }
    } catch { $("#bulkOut").innerHTML = `<div class="uerr">Couldn't reach the upload API.</div>`; }
    finally { btn.textContent = original; $("#bulkCheck").disabled = $("#bulkScore").disabled = !bulkText; }
  }
  $("#bulkCheck")?.addEventListener("click", () => sendBulk(true));
  $("#bulkScore")?.addEventListener("click", () => sendBulk(false));

  /* ---------------- dataset replay ---------------- */
  // Dataset files aren't in the repository, so on a fresh deployment none may
  // be present: disable their buttons instead of letting a click 404.
  fetch("/replay/status").then((r) => r.json()).then((st) => {
    const have = new Set((st.available || []).map((d) => d.schema));
    document.querySelectorAll(".replayBtn").forEach((b) => {
      if (!have.has(b.dataset.schema)) {
        b.disabled = true; b.title = "Dataset not present on this server — see docs/REAL_DATA.md";
      }
    });
    const blend = $("#blendBtn");
    if (blend && !have.has("upi")) { blend.disabled = true; blend.title = "Needs the UPI-style dataset — see docs/REAL_DATA.md"; }
    if (!have.size) $("#replayStatus").textContent = "No datasets are installed on this server (they aren't part of the repository) — see docs/REAL_DATA.md.";
  }).catch(() => {});
  document.querySelectorAll(".replayBtn").forEach((btn) => {
    const original = btn.textContent;
    btn.addEventListener("click", async () => {
      const schema = btn.dataset.schema, status = $("#replayStatus");
      btn.disabled = true; btn.textContent = "Replaying…";
      try {
        const r = await fetch("/replay", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ schema_name: schema, limit: 100 }) });
        const j = await r.json();
        if (!r.ok) {
          status.innerHTML = `No dataset found yet. ${j.detail || "See docs/DATA.md to download one."}`;
          return;
        }
        status.textContent = `Replayed ${j.replayed} real transactions (${j.cursor}/${j.total_rows} so far from ${schema}).`;
        toast(`Streamed ${j.replayed} ${schema} dataset transactions into the live feed.`);
      } catch { status.textContent = "Couldn't reach the replay API."; }
      finally { btn.disabled = !!btn.title; btn.textContent = original; }
    });
  });

  /* ------------- blended real+synthetic replay -------------- */
  $("#blendBtn")?.addEventListener("click", async (e) => {
    const btn = e.target, status = $("#blendStatus"), original = btn.textContent;
    btn.disabled = true; btn.textContent = "Replaying…";
    try {
      const r = await fetch("/replay/blended", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ real_schema: "upi", limit: 100, real_share: 0.5 }) });
      const j = await r.json();
      if (!r.ok) {
        status.innerHTML = `No dataset found yet. ${j.detail || "See docs/DATA.md to download one."}`;
        return;
      }
      if (j.errors && j.errors.length) {
        status.innerHTML = `Scored ${j.scored}/${j.requested} — <b>${j.errors.length} row(s) errored</b> (see console).`;
        console.error("Blended replay errors:", j.errors);
        toast(`Blended replay hit ${j.errors.length} error(s) — check the browser console.`);
      } else {
        status.textContent = `Scored ${j.scored}/${j.requested} with zero errors — ${j.real_scored} UPI-style dataset rows + ${j.synth_scored} synthetic, merged chronologically (${j.real_cursor}/${j.real_total_rows} dataset rows used so far).`;
        toast(`Streamed ${j.scored} blended transactions (dataset + synthetic) into the live feed — no errors.`);
      }
    } catch { status.textContent = "Couldn't reach the blended replay API."; }
    finally { btn.disabled = false; btn.textContent = original; }
  });


  /* ---------------- age cohort breakdown ---------------- */
  // distinct from the global pct(): a cohort with no denominator reports "—",
  // never "0.0%", so an empty bracket can't be misread as a perfect score
  const ratePct = (x) => (x === null || x === undefined ? "—" : (x * 100).toFixed(1) + "%");

  async function loadAgeBreakdown() {
    const body = $("#ageBody"), btn = $("#ageRefresh");
    if (!body) return;
    if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
    try {
      const r = await fetch("/metrics/by_age");
      if (!r.ok) throw new Error(r.statusText);
      const j = await r.json();
      const rows = (j.brackets || []).filter((b) => b.transactions > 0);
      if (!rows.length) {
        body.innerHTML = `<div class="hint">No scored transactions yet — run the simulator or a replay, then Refresh.</div>`;
        return;
      }
      const maxTx = Math.max(...rows.map((b) => b.transactions));
      body.innerHTML = `
        <table class="agetable">
          <thead><tr>
            <th>Age</th><th>Transactions</th><th>Fraud seen</th>
            <th title="Share of this cohort's fraud that Sentinel stopped">Caught</th>
            <th title="Share of this cohort's LEGITIMATE transactions that Sentinel stopped — the fairness-relevant number">False alarms</th>
            <th>Avg amount</th>
          </tr></thead>
          <tbody>${rows.map((b) => `
            <tr>
              <td><span class="agechip age-${b.bracket.replace("+","p")}">${b.bracket}</span></td>
              <td><span class="agebar" style="--w:${(b.transactions / maxTx * 100).toFixed(1)}%"></span>${b.transactions}</td>
              <td>${b.fraud}</td>
              <td class="${b.detection_rate !== null && b.detection_rate < 0.5 ? "warn" : ""}">${ratePct(b.detection_rate)}</td>
              <td class="${b.false_positive_rate !== null && b.false_positive_rate > 0.05 ? "warn" : ""}">${ratePct(b.false_positive_rate)}</td>
              <td>${b.avg_amount === null ? "—" : money0(b.avg_amount)}</td>
            </tr>`).join("")}
          </tbody>
        </table>
        <div class="hint agefoot">
          ${j.fpr_gap === null
            ? "Fairness gap needs legitimate traffic in at least two cohorts."
            : `<b>Fairness gap:</b> ${ratePct(j.fpr_gap)} spread between the most- and least-affected cohort's false-alarm rate.
               ${j.fpr_gap > 0.05 ? "A gap this wide is what a bank's model-risk review would ask about." : "Narrow — no cohort is being disproportionately stopped."}`}
          <span class="agenote">${j.note}</span>
        </div>`;
    } catch {
      body.innerHTML = `<div class="hint">Couldn't load the age breakdown.</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Refresh"; }
    }
  }
  $("#ageRefresh")?.addEventListener("click", loadAgeBreakdown);

  /* ---------------- model evaluation report ---------------- */
  const nicePct = (x, d = 1) => (x === null || x === undefined ? "—" : (x * 100).toFixed(d) + "%");
  const SCEN_LABEL = {
    account_takeover: "Account takeover", card_testing: "Card testing", stolen_card_geo: "Stolen card abroad",
    bust_out: "Bust-out", amount_just_under: "Just under the limit", slow_drip: "Slow drip",
    geo_consistent_ato: "Stealth takeover",
  };

  async function loadEvaluation() {
    const body = $("#evalBody");
    if (!body) return;
    try {
      const r = await fetch("/evaluation");
      if (!r.ok) {
        body.innerHTML = `<div class="hint">No evaluation report yet — run <code>python -m sentinel.eval</code>.</div>`;
        return;
      }
      const e = await r.json();
      const m = e.metrics, cm = e.confusion_matrix, ci = e.confidence_intervals_95 || {}, ds = e.dataset;
      const ciTxt = (k, d = 1) => ci[k] ? `95% CI ${nicePct(ci[k][0], d)}–${nicePct(ci[k][1], d)}` : "";
      const tile = (label, val, sub, tip) =>
        `<div class="etile" title="${tip}"><div class="el">${label}</div><div class="ev">${val}</div><div class="es">${sub}</div></div>`;
      const scen = Object.entries(e.per_scenario || {}).map(([k, v]) => `
        <div class="srow">
          <span class="sname">${SCEN_LABEL[k] || k}${v.adversarial ? ' <em>evasive</em>' : ""}</span>
          <span class="sbar"><i style="width:${(v.recall || 0) * 100}%"></i></span>
          <span class="sval">${nicePct(v.recall, 0)} <small>(${v.caught}/${v.fraud})</small></span>
        </div>`).join("");
      $("#evalLead").textContent =
        `Scored on ${ds.transactions.toLocaleString("en-IN")} transactions from ${ds.customers} customers the model never saw during training ` +
        `(${ds.fraud} fraud, ${nicePct(ds.fraud_prevalence, 2)}). Report generated ${new Date(e.generated_at).toLocaleDateString("en-IN")}.`;
      body.innerHTML = `
        <div class="etiles">
          ${tile("Fraud caught", nicePct(m.recall), ciTxt("recall"), "Recall: share of fraudulent transactions stopped (blocked or challenged)")}
          ${tile("Alerts that were fraud", nicePct(m.precision), ciTxt("precision"), "Precision: of the transactions stopped, the share that really were fraud")}
          ${tile("False Positive Rate — BLOCK ONLY", nicePct(e.block_only.false_positive_rate, 3) + " ✅", `Customer friction — BLOCK + CHALLENGE: ${nicePct(m.false_positive_rate, 2)}`, "False-positive rate counts only legitimate transactions that were actually BLOCKED. Challenges are shown separately as customer friction.")}
          ${tile("F1 score", nicePct(m.f1), ciTxt("f1"), "Harmonic mean of precision and recall")}
          ${tile("ROC-AUC", e.threshold_free.roc_auc ?? "—", "ranking quality, 1.0 = perfect", "Probability a random fraud is scored above a random genuine transaction")}
          ${tile("PR-AUC", e.threshold_free.pr_auc ?? "—", `random guess = ${e.threshold_free.pr_auc_baseline}`, "Area under the precision-recall curve — the more informative ranking metric when fraud is rare")}
        </div>
        <div class="egrid">
          <div>
            <h4>Confusion matrix</h4>
            <table class="cmtable">
              <tr><th></th><th>Stopped</th><th>Allowed</th></tr>
              <tr><th>Fraud</th><td class="good">${cm.tp.toLocaleString("en-IN")}<small>caught</small></td><td class="bad">${cm.fn.toLocaleString("en-IN")}<small>missed</small></td></tr>
              <tr><th>Genuine</th><td class="bad">${cm.fp.toLocaleString("en-IN")}<small>false alarm</small></td><td class="good">${cm.tn.toLocaleString("en-IN")}<small>correctly allowed</small></td></tr>
            </table>
            <div class="hint emeta">MCC ${m.mcc === null ? "—" : m.mcc.toFixed(3)} · balanced accuracy ${nicePct(m.balanced_accuracy)} ·
              calibration error ${e.calibration.ece} · money protected ${nicePct(e.money.prevented_share)} of ₹${Math.round(e.money.fraud_amount_inr).toLocaleString("en-IN")}</div>
          </div>
          <div>
            <h4>Fraud caught, by attack type</h4>
            ${scen}
          </div>
        </div>
        <div class="hint agefoot">Age fairness on this test: false-alarm rates differ by at most
          <b>${nicePct(e.fairness.false_positive_rate_gap, 2)}</b> between age groups.
          <span class="agenote">Synthetic held-out test — validate on the bank's own labelled history before production use. Full report: docs/EVALUATION.md</span></div>`;
    } catch {
      body.innerHTML = `<div class="hint">Couldn't load the evaluation report.</div>`;
    }
  }

  /* ---------------- boot ---------------- */
  fetch("/health").then((r) => r.json()).then((h) => {
    if (h.simulator === false) {
      simulatorOn = false;
      ["#sim", "#blendBlock"].forEach((sel) => { const el = $(sel); if (el) el.style.display = "none"; });
    }
    if (h.serverless) startPolling(); else connect();
  }).catch(connect);
  loadAgeBreakdown();
  loadEvaluation();
})();
