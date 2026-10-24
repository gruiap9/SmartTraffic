// CivicSignal sidebar: Impact, Policy, Events, Inspect tabs + follow card.
"use strict";

const Sidebar = (() => {
  const PRESETS = {
    Balanced: { flow: 1, climate: 1, transit: 0.5, fairness: 0 },
    Commute:  { flow: 3, climate: 0.5, transit: 0.5, fairness: 0 },
    Climate:  { flow: 0.5, climate: 3, transit: 1, fairness: 0 },
    Transit:  { flow: 1, climate: 1, transit: 3, fairness: 0 },
    Fairness: { flow: 1, climate: 0.5, transit: 0.5, fairness: 3 },
  };
  const SLIDERS = ["flow", "climate", "transit", "fairness"];

  // Yearly scaling: 2 rush hours x 250 weekdays.
  const YEAR_FACTOR = 2 * 250;

  let state = null;
  let impactWindow = "start";          // start | recent
  let snaps = [];                       // per-frame [t, {pane:{...m}}]
  let lastRender = 0;
  let pendingFrame = null;
  let trailingTimer = null;
  let raceState = {};                   // pane -> {elapsed, trip, done}
  let eventLog = [];

  function $(sel, root = document) { return root.querySelector(sel); }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls; if (text != null) e.textContent = text;
    return e;
  }

  // ---- init / tabs ----
  function init() {
    document.querySelectorAll(".tab").forEach(t => {
      t.onclick = () => showTab(t.dataset.tab);
    });
    document.querySelectorAll(".seg-btn").forEach(b => {
      b.onclick = () => {
        document.querySelectorAll(".seg-btn").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        impactWindow = b.dataset.window;
        renderImpact(true);
      };
    });
    document.querySelectorAll(".popout").forEach(p => {
      p.onclick = () => openPopout(p.dataset.view);
    });
    buildPolicy();
    wireEvents();
    $("#follow-clear").onclick = () => CS.sendFollow(null);

    // URL options.
    const u = CS.URL_OPTS;
    if (u.tab) showTab(u.tab);
    if (u.ask) { const ta = $("#copilot-goal"); if (ta) ta.value = u.ask;
      setTimeout(askCopilot, 300); }
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach(t =>
      t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach(p =>
      p.classList.toggle("active", p.dataset.panel === name));
  }

  function showOnly(view) {
    showTab(view);
    document.querySelectorAll(".tabs").forEach(t => t.style.display = "none");
  }

  function openPopout(view) {
    const url = new URL(location.href);
    url.searchParams.set("view", view);
    window.open(url.toString(), "cs_" + view, "width=560,height=860");
  }

  // ---- state / frame hooks ----
  function onState(s) {
    state = s;
    // Event button labels from meta.
    const m = s.meta || {};
    setLabel("#ev-amb .ev-label", m.ambulance || "Ambulance");
    setLabel("#ev-surge .ev-label", m.surge || "Surge");
    setLabel("#ev-crash .ev-label", m.crash || "Crash");
    // Sync policy UI to shared weights.
    syncPolicy(s.weights);
    // Reset snapshots on scenario/pane change.
    if (s.t === 0) { snaps = []; raceState = {}; }
  }

  function onFrame(frame) {
    // Snapshot every pane's metrics.
    const snap = {};
    for (const [pane, sim] of Object.entries(frame.sims)) snap[pane] = sim.m;
    snaps.push([frame.t, snap]);
    if (snaps.length > 2600) snaps.shift();

    updateRace(frame);
    if (frame.inspect) renderInspect(frame);

    // Throttle impact render to <= every 400ms, with a trailing update.
    const now = Date.now();
    pendingFrame = frame;
    if (now - lastRender >= 400) {
      lastRender = now; renderImpact();
    } else if (!trailingTimer) {
      trailingTimer = setTimeout(() => {
        trailingTimer = null; lastRender = Date.now(); renderImpact();
      }, 400 - (now - lastRender));
    }
  }

  function setLabel(sel, text) { const e = $(sel); if (e) e.textContent = text; }

  // ---- IMPACT ----
  function panes() { return state ? state.panes : []; }
  function baselinePane() {
    // The non-adaptive pane to compare against (first baseline present).
    for (const p of panes()) if (p !== "adaptive") return p;
    return null;
  }

  function windowDelta(pane, attr) {
    if (!snaps.length) return 0;
    const last = snaps[snaps.length - 1][1][pane];
    if (!last) return 0;
    if (impactWindow === "start") return last[attr];
    // recent = last 2 min (120s) delta.
    const tNow = snaps[snaps.length - 1][0];
    let base = null;
    for (let i = snaps.length - 1; i >= 0; i--) {
      if (tNow - snaps[i][0] >= 120) { base = snaps[i][1][pane]; break; }
    }
    if (!base) base = snaps[0][1][pane];
    return (last[attr] - (base[attr] || 0));
  }

  function renderImpact(force) {
    if (!state) return;
    const civ = "adaptive";
    const base = baselinePane();
    if (!panes().includes(civ) || !base) { $("#impact-heroes").innerHTML = ""; return; }

    // Hero tiles: delay %, CO2 %, and ambulance diff (or stops %).
    const heroes = $("#impact-heroes"); heroes.innerHTML = "";
    heroes.appendChild(heroTile("Delay",
      pct(windowDelta(civ, "avg_delay_s"), windowDelta(base, "avg_delay_s")), true));
    heroes.appendChild(heroTile("CO₂",
      pct(deltaMetric(civ, "co2_kg"), deltaMetric(base, "co2_kg")), true));

    const ambCiv = lastM(civ, "amb_last_trip_s"), ambBase = lastM(base, "amb_last_trip_s");
    if (ambCiv > 0 || ambBase > 0) {
      const diff = ambBase - ambCiv;
      heroes.appendChild(heroTile("🚑 saved", (diff >= 0 ? "+" : "") +
        diff.toFixed(0) + "s", diff >= 0));
    } else {
      heroes.appendChild(heroTile("Stops",
        pct(deltaMetric(civ, "stops"), deltaMetric(base, "stops")), true));
    }

    renderScoreboard();
    renderYearly(civ, base);
  }

  function lastM(pane, attr) {
    if (!snaps.length) return 0;
    const s = snaps[snaps.length - 1][1][pane];
    return s ? (s[attr] || 0) : 0;
  }
  function deltaMetric(pane, attr) { return windowDelta(pane, attr); }

  function pct(civ, base) {
    if (!base) return "—";
    const p = (civ - base) / Math.abs(base) * 100;
    return (p >= 0 ? "+" : "") + p.toFixed(0) + "%";
  }

  function heroTile(lab, value) {
    const t = el("div", "hero");
    // Green when CivicSignal improves: a '-' % (less delay/CO2) or '+' saved.
    let cls = "";
    if (lab === "🚑 saved") cls = String(value).startsWith("+") ? "good" : "bad";
    else cls = String(value).startsWith("-") ? "good" : (value === "—" ? "" : "bad");
    if (cls) t.classList.add(cls);
    t.appendChild(Object.assign(el("div", "big"), { textContent: value }));
    t.appendChild(Object.assign(el("div", "lab"),
      { textContent: `CivicSignal vs ${labelOf(baselinePane())}` }));
    const sub = el("div", "lab"); sub.textContent = lab; t.insertBefore(sub, t.lastChild);
    return t;
  }

  function labelOf(pane) { return state ? (state.labels[pane] || pane) : pane; }

  // scoreboard: traffic / climate&health / civic categories, star best.
  const SCORE_ROWS = [
    ["traffic", [["Delay s", "avg_delay_s", true], ["Queue", "queue", true],
                 ["Stops", "stops", true]]],
    ["climate & health", [["CO₂ kg", "co2_kg", true], ["Fuel L", "fuel_l", true],
                          ["NOx g", "nox_g", true], ["PM g", "pm_g", true]]],
    ["civic", [["Bus trip s", "bus_avg_trip_s", true],
               ["Amb trip s", "amb_last_trip_s", true]]],
  ];

  function renderScoreboard() {
    const host = $("#impact-scoreboard"); host.innerHTML = "";
    const ps = panes();
    const tbl = el("table");
    const compact = ps.length > 2;
    $("#impact-scoreboard").className = "scoreboard" + (compact ? " compact" : "");
    const thead = el("tr");
    thead.appendChild(el("th", null, ""));
    for (const p of ps) thead.appendChild(el("th", null, labelOf(p)));
    tbl.appendChild(thead);

    for (const [cat, rows] of SCORE_ROWS) {
      const catTr = el("tr");
      const td = el("td", "cat", cat); td.colSpan = ps.length + 1;
      catTr.appendChild(td); tbl.appendChild(catTr);
      for (const [lab, attr, lower] of rows) {
        const tr = el("tr");
        tr.appendChild(el("td", null, lab));
        const vals = ps.map(p => lastM(p, attr));
        const best = lower ? Math.min(...vals.filter(v => v > 0).concat([Infinity]))
                           : Math.max(...vals);
        ps.forEach((p, i) => {
          const v = vals[i];
          const cell = el("td", null, fmt(v, attr));
          if (v === best && v > 0) {
            const s = el("span", "star", " ★"); cell.appendChild(s);
          }
          tr.appendChild(cell);
        });
        tbl.appendChild(tr);
      }
    }
    host.appendChild(tbl);
  }

  function fmt(v, attr) {
    if (attr === "co2_kg" || attr === "fuel_l") return v.toFixed(1);
    if (attr === "pm_g") return v.toFixed(2);
    if (attr === "avg_delay_s" || attr === "nox_g") return v.toFixed(1);
    if (attr === "stops") return v.toFixed(0);
    return v.toFixed(0);
  }

  function renderYearly(civ, base) {
    const host = $("#impact-yearly"); host.innerHTML = "";
    // Savings this run = baseline - civic (positive = saved).
    const co2Saved = (lastM(base, "co2_kg") - lastM(civ, "co2_kg"));
    const fuelSaved = (lastM(base, "fuel_l") - lastM(civ, "fuel_l"));
    const hoursSaved = (lastM(base, "halt_vs") - lastM(civ, "halt_vs")) / 3600;
    const gal = fuelSaved * 0.264172;
    const dollars = gal * 3.50;

    host.appendChild(Object.assign(el("div", "yhead"),
      { textContent: "Yearly estimate (back-of-envelope)" }));
    host.appendChild(yrow("Hours of delay saved", (hoursSaved * YEAR_FACTOR).toFixed(0)));
    host.appendChild(yrow("CO₂ saved (t)", (co2Saved * YEAR_FACTOR / 1000).toFixed(1)));
    host.appendChild(yrow("Fuel saved (gal)", (gal * YEAR_FACTOR).toFixed(0)));
    host.appendChild(yrow("Dollars saved", "$" + (dollars * YEAR_FACTOR).toFixed(0)));
    host.appendChild(Object.assign(el("div", "ynote"),
      { textContent: "Scales this run to 2 rush hours × 250 weekdays." }));
  }
  function yrow(k, v) {
    const r = el("div", "yrow");
    r.appendChild(el("span", null, k)); r.appendChild(Object.assign(el("span"),
      { textContent: v, style: "font-weight:700" }));
    return r;
  }

  // ---- POLICY ----
  function buildPolicy() {
    const pr = $("#presets");
    for (const name of Object.keys(PRESETS)) {
      const b = el("button", "preset", name);
      b.onclick = () => applyPreset(name);
      pr.appendChild(b);
    }
    const host = $("#sliders");
    for (const k of SLIDERS) {
      const row = el("div", "slider-row");
      row.appendChild(el("label", null, k));
      const inp = document.createElement("input");
      inp.type = "range"; inp.min = 0; inp.max = 3; inp.step = 0.1; inp.value = 1;
      inp.dataset.k = k;
      inp.oninput = () => { $(`.val[data-k="${k}"]`).textContent =
        parseFloat(inp.value).toFixed(1); sendWeights(); };
      row.appendChild(inp);
      const val = el("span", "val", "1.0"); val.dataset.k = k;
      row.appendChild(val);
      host.appendChild(row);
    }
    $("#preempt-toggle").onchange = sendWeights;
    $("#copilot-ask").onclick = askCopilot;
  }

  function applyPreset(name) {
    const p = PRESETS[name];
    for (const k of SLIDERS) {
      const inp = $(`input[data-k="${k}"]`); inp.value = p[k];
      $(`.val[data-k="${k}"]`).textContent = p[k].toFixed(1);
    }
    sendWeights();
  }

  function syncPolicy(w) {
    if (!w) return;
    for (const k of SLIDERS) {
      const inp = $(`input[data-k="${k}"]`); if (!inp) continue;
      inp.value = w[k]; $(`.val[data-k="${k}"]`).textContent = (+w[k]).toFixed(1);
    }
    const pt = $("#preempt-toggle"); if (pt) pt.checked = !!w.preempt;
  }

  function currentWeights() {
    const w = { preempt: $("#preempt-toggle").checked };
    for (const k of SLIDERS) w[k] = parseFloat($(`input[data-k="${k}"]`).value);
    return w;
  }
  function sendWeights() { CS.send({ type: "weights", weights: currentWeights() }); }

  async function askCopilot() {
    const goal = $("#copilot-goal").value.trim();
    if (!goal) return;
    const res = $("#copilot-result");
    res.innerHTML = "<span class='cexp'>Asking the co-pilot…</span>";
    try {
      const r = await fetch("/api/copilot", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }),
      });
      const data = await r.json();
      if (data.error) { res.innerHTML =
        `<span class='cexp'>Co-pilot unavailable: ${data.error}</span>`; return; }
      renderCopilot(data);
    } catch (e) {
      res.innerHTML = `<span class='cexp'>Error: ${e}</span>`;
    }
  }

  function renderCopilot(d) {
    const res = $("#copilot-result"); res.innerHTML = "";
    res.appendChild(el("div", "ctitle", d.title || "Proposed policy"));
    res.appendChild(el("div", "cexp", d.explanation || ""));
    const w = el("div", "cexp",
      `flow ${d.flow} · climate ${d.climate} · transit ${d.transit} · ` +
      `fairness ${d.fairness} · preempt ${d.preempt ? "on" : "off"}`);
    res.appendChild(w);
    const apply = el("div", "capply");
    const b1 = el("button", null, "Apply policy");
    b1.onclick = () => { syncPolicy(d); sendWeights(); };
    apply.appendChild(b1);
    if (d.event && d.event !== "none") {
      const b2 = el("button", null, `Apply + simulate ${d.event}`);
      b2.onclick = () => { syncPolicy(d); sendWeights();
        CS.send({ type: "event", event: d.event }); };
      apply.appendChild(b2);
    }
    res.appendChild(apply);
  }

  // ---- EVENTS ----
  function wireEvents() {
    $("#ev-amb").onclick = () => CS.send({ type: "event", event: "ambulance" });
    $("#ev-surge").onclick = () => CS.send({ type: "event", event: "surge" });
    $("#ev-crash").onclick = () => CS.send({ type: "event", event: "crash" });
  }

  function logEvent(t, label) {
    eventLog.unshift(`${fmtClock(t)}  ${label}`);
    eventLog = eventLog.slice(0, 40);
    const host = $("#event-log"); if (!host) return;
    host.innerHTML = "";
    for (const line of eventLog) host.appendChild(el("div", null, line));
    // reset race when an ambulance is dispatched
    if (/ambulance|🚑|Mount Sinai/i.test(label)) raceState = {};
  }

  function updateRace(frame) {
    const host = $("#amb-race"); if (!host) return;
    let any = false;
    for (const [pane, sim] of Object.entries(frame.sims)) {
      const m = sim.m;
      if (m.amb_active > 0) {
        any = true;
        raceState[pane] = { elapsed: m.amb_elapsed_s, done: false,
                            trip: raceState[pane] ? raceState[pane].trip : 0 };
      } else if (raceState[pane] && !raceState[pane].done && m.amb_last_trip_s > 0) {
        raceState[pane].done = true;
        raceState[pane].trip = m.amb_last_trip_s;
        CS.toast(`🚑 ${labelOf(pane)} arrived in ${m.amb_last_trip_s.toFixed(0)}s`);
      }
    }
    if (Object.keys(raceState).length === 0) { host.innerHTML = ""; return; }
    // Render bars (normalise to the slowest).
    const maxT = Math.max(1, ...Object.values(raceState).map(
      r => r.done ? r.trip : r.elapsed));
    host.innerHTML = "";
    for (const pane of panes()) {
      const r = raceState[pane]; if (!r) continue;
      const row = el("div", "race-row" + (r.done ? " done" : ""));
      const t = r.done ? r.trip : r.elapsed;
      row.appendChild(Object.assign(el("div"),
        { textContent: `${labelOf(pane)} — ${t.toFixed(0)}s${r.done ? " ✓" : ""}` }));
      const bg = el("div", "bar-bg");
      const bar = el("div", "bar");
      bar.style.width = (t / maxT * 100).toFixed(0) + "%";
      bar.style.background = CS.COLORS[pane];
      bg.appendChild(bar); row.appendChild(bg); host.appendChild(row);
    }
  }

  // ---- INSPECT ----
  function renderInspect(frame) {
    const insp = frame.inspect;
    const nameEl = $("#inspect-name");
    const body = $("#inspect-body"); body.innerHTML = "";
    const tls = CS.App.inspected;
    if (!tls || !insp) { nameEl.textContent = "Click a light to inspect it"; return; }
    nameEl.textContent = "Intersection " + tls;

    for (const pane of panes()) {
      const data = insp[pane]; if (!data) continue;
      const sim = frame.sims[pane];
      const card = el("div", "insp-pane");
      card.appendChild(Object.assign(el("div", "ip-name"),
        { textContent: labelOf(pane), style: "color:" + CS.COLORS[pane] }));

      // approach stats from the light's sig + an aggregate
      const m = sim.m;
      card.appendChild(Object.assign(el("div", "ip-stats"),
        { textContent: `queue ${m.queue} · moving ${m.running} · ` +
          `max wait ${m.max_wait_s}s` }));

      if (pane === "adaptive" && data.explain) {
        const ex = data.explain;
        card.appendChild(el("div", "insp-reason", ex.reason || ""));
        for (const o of (ex.options || [])) {
          const row = el("div", "opt-row" + (o.current ? " current" :
            (o.target ? " target" : "")));
          const lab = el("div", "opt-label");
          lab.appendChild(Object.assign(el("span"),
            { textContent: (o.streets || []).join(", ").slice(0, 30) +
              (o.current ? " (now)" : o.target ? " (next)" : "") }));
          lab.appendChild(Object.assign(el("span"),
            { textContent: o.total.toFixed(1) }));
          row.appendChild(lab);
          row.appendChild(termBar(o.terms));
          card.appendChild(row);
        }
      } else {
        // baseline description + next switch
        card.appendChild(el("div", "insp-reason", baselineDesc(pane)));
        if (data.next_switch != null) {
          card.appendChild(Object.assign(el("div", "ip-stats"),
            { textContent: `next switch in ${Math.max(0, data.next_switch)}s` }));
        }
      }
      body.appendChild(card);
    }
  }

  function termBar(terms) {
    const bar = el("div", "opt-bar");
    const colors = { flow: "#60a5fa", climate: "#22c55e",
                     transit: "#c084fc", fairness: "#f5a524" };
    const total = Object.values(terms).reduce((a, b) => a + Math.abs(b), 0) || 1;
    for (const k of ["flow", "climate", "transit", "fairness"]) {
      const v = Math.abs(terms[k] || 0);
      const seg = el("div", "opt-seg");
      seg.style.width = (v / total * 100).toFixed(0) + "%";
      seg.style.background = colors[k];
      seg.style.opacity = (terms[k] || 0) < 0 ? 0.4 : 1;
      seg.title = `${k}: ${(terms[k] || 0).toFixed(2)}`;
      bar.appendChild(seg);
    }
    return bar;
  }

  function baselineDesc(pane) {
    if (pane === "fixed") return "Fixed 90s program; switches on a fixed timer.";
    if (pane === "actuated") return "Detectors extend green; switches when the gap grows.";
    if (pane === "greenwave") return "Coordinated offsets; switches on the wave schedule.";
    return "";
  }

  // ---- FOLLOW ----
  function onFollow(follow) {
    const card = $("#follow-card");
    const body = $("#follow-body");
    if (!CS.App.followed) { card.style.display = "none"; return; }
    card.style.display = "";
    body.innerHTML = "";
    for (const pane of panes()) {
      const d = follow[pane]; if (!d) continue;
      const row = el("div", "follow-row");
      row.appendChild(Object.assign(el("span"),
        { textContent: labelOf(pane), style: "color:" + CS.COLORS[pane] }));
      let txt = "—";
      if (d.arrived) txt = `arrived (${d.elapsed}s)`;
      else if (d.present) txt = `${d.elapsed}s`;
      else if (d.depart == null) txt = "not departed";
      row.appendChild(Object.assign(el("span"), { textContent: txt }));
      body.appendChild(row);
    }
  }

  function fmtClock(t) {
    const m = Math.floor(t / 60), s = t % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  return { init, onState, onFrame, onFollow, logEvent, showOnly };
})();

window.Sidebar = Sidebar;
