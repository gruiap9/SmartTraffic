// CivicSignal dashboard — websocket, canvas maps, chart strip.
"use strict";

const COLORS = {
  fixed: "#f5a524", actuated: "#c084fc", greenwave: "#60a5fa", adaptive: "#2dd4bf",
  road: "#2a3346", roadEdge: "#3a4660", junction: "#323c52",
  building: "#161b27", park: "#16281f",
  car: "#aeb8c8", halt: "#ef4444", bus: "#3b82f6", amb: "#ef4444", crash: "#fb923c",
  label: "#8b98ad", landmark: "#e6edf6",
};
const MARGIN_M = 95;      // frame signalized area + 95 m
const CAR_L = 4.5, CAR_W = 2.0, BUS_L = 12, BUS_W = 2.6;

// ---- URL options ----
const qs = new URLSearchParams(location.search);
const URL_OPTS = {
  view: qs.get("view"),
  replay: qs.get("replay"),
  t: qs.get("t") ? parseInt(qs.get("t")) : null,
  autoplay: qs.has("autoplay"),
  tab: qs.get("tab"),
  heat: qs.has("heat"),
  follow: qs.get("follow"),
  ask: qs.get("ask"),
};

// ---- global state ----
const App = {
  ws: null,
  state: null,
  scenarioData: {},        // id -> map geometry json
  panes: {},               // strategy -> PaneView
  heatOn: URL_OPTS.heat || false,
  heatMax: 1,
  lastFrame: null,
  history: [],
  followed: URL_OPTS.follow || null,
  inspected: null,
  chart: null,
};

// ======================================================================
// Coordinate transform per pane
// ======================================================================
class Transform {
  constructor(mapData, rotateDeg) {
    this.rotate = (rotateDeg || 0) * Math.PI / 180;
    // Signalized bounding box (lights) + margin, in world coords.
    const lights = mapData.lights || [];
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    if (lights.length) {
      for (const l of lights) {
        minx = Math.min(minx, l.x); maxx = Math.max(maxx, l.x);
        miny = Math.min(miny, l.y); maxy = Math.max(maxy, l.y);
      }
    } else {
      [minx, miny, maxx, maxy] = mapData.bounds;
    }
    this.world = { minx: minx - MARGIN_M, miny: miny - MARGIN_M,
                   maxx: maxx + MARGIN_M, maxy: maxy + MARGIN_M };
    this.cx = (this.world.minx + this.world.maxx) / 2;
    this.cy = (this.world.miny + this.world.maxy) / 2;
    this.quarter = false;   // optional extra 90° turn for tall panes
    this.scale = 1; this.ox = 0; this.oy = 0;
  }

  // Rotate world point around centre so avenues run vertically.
  _rot(x, y) {
    const dx = x - this.cx, dy = y - this.cy;
    let a = this.rotate + (this.quarter ? Math.PI / 2 : 0);
    const c = Math.cos(a), s = Math.sin(a);
    // world y is up; rotate clockwise-from-north convention.
    const rx = dx * c + dy * s;
    const ry = -dx * s + dy * c;
    return [rx, ry];
  }

  fit(wpx, hpx) {
    // Compute rotated extent of the four corners.
    const corners = [
      [this.world.minx, this.world.miny], [this.world.maxx, this.world.miny],
      [this.world.maxx, this.world.maxy], [this.world.minx, this.world.maxy],
    ];
    const tryFit = (quarter) => {
      this.quarter = quarter;
      let rminx = Infinity, rminy = Infinity, rmaxx = -Infinity, rmaxy = -Infinity;
      for (const [x, y] of corners) {
        const [rx, ry] = this._rot(x, y);
        rminx = Math.min(rminx, rx); rmaxx = Math.max(rmaxx, rx);
        rminy = Math.min(rminy, ry); rmaxy = Math.max(rmaxy, ry);
      }
      const w = rmaxx - rminx, h = rmaxy - rminy;
      const scale = Math.min(wpx / w, hpx / h);
      return { scale, w, h, rminx, rminy };
    };
    const a = tryFit(false);
    const b = tryFit(true);
    const best = b.scale > a.scale ? b : a;
    this.quarter = (best === b);
    const f = this.quarter ? b : a;
    this.scale = f.scale * 0.96;
    // Centre the drawing.
    this.ox = (wpx - f.w * this.scale) / 2 - f.rminx * this.scale;
    this.oy = (hpx - f.h * this.scale) / 2 - f.rminy * this.scale;
  }

  // world -> screen px. Screen y is down, world y is up -> flip handled in _rot.
  toScreen(x, y) {
    const [rx, ry] = this._rot(x, y);
    return [this.ox + rx * this.scale, this.oy + ry * this.scale];
  }

  // world angle (deg, clockwise from north) -> screen angle (rad, for ctx)
  angleToScreen(deg) {
    // In screen space (y down), a north-up heading needs adjusting by rotation.
    let a = (deg) * Math.PI / 180;      // clockwise from north
    a += this.rotate + (this.quarter ? Math.PI / 2 : 0);
    return a;   // used with a convention in drawVehicle
  }
}

// ======================================================================
// PaneView — one strategy's map + canvases
// ======================================================================
class PaneView {
  constructor(strategy, el) {
    this.strategy = strategy;
    this.el = el;
    el.dataset.strat = strategy;
    this.nameEl = el.querySelector(".pane-name");
    this.stQ = el.querySelector(".st-q");
    this.stM = el.querySelector(".st-m");
    this.stD = el.querySelector(".st-d");
    this.stAwrap = el.querySelector(".amb");
    this.stA = el.querySelector(".st-a");
    this.cBase = el.querySelector(".c-base");
    this.cHeat = el.querySelector(".c-heat");
    this.cLive = el.querySelector(".c-live");
    this.wrap = el.querySelector(".canvas-wrap");
    this.nameEl.textContent = (App.state.labels || {})[strategy] || strategy;
    this.nameEl.style.color = COLORS[strategy];
    this.transform = null;
    this.mapData = null;
    this.baseDrawn = false;
    this.lastHeatDraw = 0;

    this.cLive.addEventListener("click", (e) => this.onClick(e));
  }

  setMap(mapData) {
    this.mapData = mapData;
    this.transform = new Transform(mapData, mapData.meta.rotate_deg);
    this.baseDrawn = false;
    this.resize();
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.wrap.clientWidth, h = this.wrap.clientHeight;
    if (w === 0 || h === 0) return;
    for (const c of [this.cBase, this.cHeat, this.cLive]) {
      c.width = w * dpr; c.height = h * dpr;
    }
    this.pxW = w * dpr; this.pxH = h * dpr;
    this.dpr = dpr;
    if (this.transform) {
      this.transform.fit(this.pxW, this.pxH);
      this.drawBase();
    }
  }

  drawBase() {
    if (!this.mapData || !this.transform) return;
    const ctx = this.cBase.getContext("2d");
    const T = this.transform;
    ctx.clearRect(0, 0, this.pxW, this.pxH);
    ctx.fillStyle = "#0b0e14";
    ctx.fillRect(0, 0, this.pxW, this.pxH);

    // Buildings / parks.
    for (const b of (this.mapData.buildings || [])) {
      ctx.beginPath();
      b.poly.forEach(([x, y], i) => {
        const [sx, sy] = T.toScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.closePath();
      ctx.fillStyle = b.kind === "park" ? COLORS.park : COLORS.building;
      ctx.fill();
    }

    // Junction polygons.
    ctx.fillStyle = COLORS.junction;
    for (const j of (this.mapData.junctions || [])) {
      if (!j.poly || j.poly.length < 3) continue;
      ctx.beginPath();
      j.poly.forEach(([x, y], i) => {
        const [sx, sy] = T.toScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.closePath();
      ctx.fill();
    }

    // Lanes (as thick strokes along their shape).
    for (const lane of this.mapData.lanes) {
      const shape = lane.shape;
      if (shape.length < 2) continue;
      ctx.beginPath();
      shape.forEach(([x, y], i) => {
        const [sx, sy] = T.toScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.lineWidth = Math.max(1.5, lane.width * T.scale);
      ctx.strokeStyle = COLORS.road;
      ctx.lineCap = "round";
      ctx.stroke();
    }

    // Street labels.
    ctx.fillStyle = COLORS.label;
    ctx.font = `${11 * this.dpr}px -apple-system, sans-serif`;
    ctx.textAlign = "center";
    for (const lab of (this.mapData.labels || [])) {
      if (!lab.name) continue;
      const [sx, sy] = T.toScreen(lab.x, lab.y);
      ctx.fillText(lab.name, sx, sy);
    }

    // Landmarks.
    ctx.fillStyle = COLORS.landmark;
    ctx.font = `600 ${12 * this.dpr}px -apple-system, sans-serif`;
    for (const lm of (this.mapData.landmarks || [])) {
      const [sx, sy] = T.toScreen(lm.x, lm.y);
      ctx.fillText("◆ " + lm.name, sx, sy);
    }

    this.baseDrawn = true;
  }

  drawHeat(simFrame) {
    const ctx = this.cHeat.getContext("2d");
    ctx.clearRect(0, 0, this.pxW, this.pxH);
    if (!App.heatOn || !simFrame || !simFrame.heat) return;
    const T = this.transform;
    const edgeMap = this._edgeShapeMap();
    for (const [eid, val] of Object.entries(simFrame.heat)) {
      const shape = edgeMap[eid];
      if (!shape) continue;
      const a = Math.min(1, val / App.heatMax);
      ctx.beginPath();
      shape.forEach(([x, y], i) => {
        const [sx, sy] = T.toScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.lineWidth = Math.max(4, 8 * T.scale);
      ctx.strokeStyle = heatColor(a);
      ctx.lineCap = "round";
      ctx.globalAlpha = 0.55;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  _edgeShapeMap() {
    if (this._edgeShapes) return this._edgeShapes;
    const m = {};
    for (const lane of this.mapData.lanes) {
      const eid = lane.id.replace(/_\d+$/, "");
      if (!m[eid]) m[eid] = lane.shape;
    }
    this._edgeShapes = m;
    return m;
  }

  drawLive(simFrame) {
    const ctx = this.cLive.getContext("2d");
    ctx.clearRect(0, 0, this.pxW, this.pxH);
    if (!simFrame) return;
    const T = this.transform;

    // Signal bars across each lane's stop line.
    const laneShapes = this._laneShapeMap();
    for (const [lane, col] of Object.entries(simFrame.sig || {})) {
      const shape = laneShapes[lane];
      if (!shape || shape.length < 2) continue;
      const p1 = shape[shape.length - 2], p2 = shape[shape.length - 1];
      const [sx, sy] = T.toScreen(p2[0], p2[1]);
      // perpendicular direction for the bar
      const [ax, ay] = T.toScreen(p1[0], p1[1]);
      let dx = sx - ax, dy = sy - ay;
      const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
      const px = -dy, py = dx;
      const bw = Math.max(4, 3.4 * T.scale);
      ctx.strokeStyle = col === "G" ? "#22c55e" : (col === "y" ? "#eab308" : "#ef4444");
      ctx.lineWidth = Math.max(2, 2.4 * this.dpr);
      ctx.beginPath();
      ctx.moveTo(sx - px * bw, sy - py * bw);
      ctx.lineTo(sx + px * bw, sy + py * bw);
      ctx.stroke();
    }

    // Vehicles.
    for (const v of simFrame.veh) {
      this.drawVehicle(ctx, v);
    }
  }

  drawVehicle(ctx, v) {
    const [id, x, y, angle, kind, halted] = v;
    const T = this.transform;
    const [sx, sy] = T.toScreen(x, y);
    // screen heading: clockwise-from-north + rotation; screen y is down.
    const a = angle * Math.PI / 180 + T.rotate + (T.quarter ? Math.PI / 2 : 0);
    let L = CAR_L, W = CAR_W, fill = COLORS.car;
    if (kind === "b") { L = BUS_L; W = BUS_W; fill = COLORS.bus; }
    else if (kind === "e") { L = 6; W = 2.4; fill = COLORS.amb; }
    else if (kind === "x") { fill = COLORS.crash; }
    else if (halted) { fill = COLORS.halt; }

    const lpx = Math.max(3, L * T.scale), wpx = Math.max(2, W * T.scale);
    ctx.save();
    ctx.translate(sx, sy);
    // heading: vehicle points "up"(north) at angle 0; convert to screen.
    ctx.rotate(a);
    if (kind === "e") {
      // pulsing halo
      const pulse = 0.5 + 0.5 * Math.sin(Date.now() / 150);
      ctx.beginPath();
      ctx.arc(0, 0, lpx * (1.3 + pulse * 0.6), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(239,68,68,${0.25 * pulse})`;
      ctx.fill();
    }
    // Draw from the front bumper: rect extends backward.
    ctx.fillStyle = fill;
    ctx.fillRect(-wpx / 2, -lpx, wpx, lpx);
    ctx.restore();

    // Follow highlight.
    if (App.followed && id === App.followed) {
      ctx.beginPath();
      ctx.arc(sx, sy, lpx * 1.8, 0, Math.PI * 2);
      ctx.strokeStyle = "#fde047"; ctx.lineWidth = 2; ctx.stroke();
    }
    // store for click hit-testing
    this._vehScreen.push([id, sx, sy, lpx]);
  }

  _laneShapeMap() {
    if (this._laneShapes) return this._laneShapes;
    const m = {};
    for (const lane of this.mapData.lanes) m[lane.id] = lane.shape;
    this._laneShapes = m;
    return m;
  }

  render(simFrame) {
    this._vehScreen = [];
    if (!this.baseDrawn) this.drawBase();
    const now = Date.now();
    if (App.heatOn && now - this.lastHeatDraw > 500) {
      this.drawHeat(simFrame); this.lastHeatDraw = now;
    } else if (!App.heatOn) {
      this.cHeat.getContext("2d").clearRect(0, 0, this.pxW, this.pxH);
    }
    this.drawLive(simFrame);
    this.updateStats(simFrame.m);
  }

  updateStats(m) {
    if (!m) return;
    this.stQ.textContent = m.queue;
    this.stM.textContent = m.running;
    this.stD.textContent = m.avg_delay_s.toFixed(1);
    if (m.amb_active > 0) {
      this.stAwrap.style.display = "";
      this.stA.textContent = m.amb_elapsed_s;
    } else {
      this.stAwrap.style.display = "none";
    }
  }

  onClick(e) {
    const rect = this.cLive.getBoundingClientRect();
    const px = (e.clientX - rect.left) * this.dpr;
    const py = (e.clientY - rect.top) * this.dpr;
    // nearest vehicle within radius -> follow
    let best = null, bestd = 18 * this.dpr;
    for (const [id, sx, sy, r] of (this._vehScreen || [])) {
      const d = Math.hypot(sx - px, sy - py);
      if (d < bestd) { bestd = d; best = id; }
    }
    if (best) { sendFollow(best); return; }
    // else nearest light -> inspect
    const T = this.transform;
    let bl = null, bld = 26 * this.dpr;
    for (const l of (this.mapData.lights || [])) {
      const [sx, sy] = T.toScreen(l.x, l.y);
      const d = Math.hypot(sx - px, sy - py);
      if (d < bld) { bld = d; bl = l.id; }
    }
    if (bl) sendInspect(bl);
  }
}

function heatColor(a) {
  // teal -> amber -> red
  const r = Math.round(40 + a * 215);
  const g = Math.round(180 - a * 140);
  const b = Math.round(160 - a * 150);
  return `rgb(${r},${g},${b})`;
}

// ======================================================================
// Layout / panes
// ======================================================================
function paneSetFor(compare) {
  if (compare === "all") return ["fixed", "actuated", "greenwave", "adaptive"];
  return [compare, "adaptive"];
}

function rebuildPanes(panes) {
  const container = document.getElementById("panes");
  container.innerHTML = "";
  container.className = panes.length > 2 ? "panes-4" : "panes-2";
  App.panes = {};
  const tpl = document.getElementById("pane-tpl");
  for (const strat of panes) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    container.appendChild(node);
    App.panes[strat] = new PaneView(strat, node);
  }
  // Assign maps once loaded.
  const data = App.scenarioData[App.state.scenario];
  if (data) for (const p of Object.values(App.panes)) p.setMap(data);
}

// ======================================================================
// WebSocket
// ======================================================================
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  App.ws = ws;
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connect, 1000); };
  ws.onmessage = (ev) => enqueue(JSON.parse(ev.data));
}

// Handle messages strictly in order (a state message may await the map file).
let msgQueue = [];
let processing = false;
function enqueue(msg) { msgQueue.push(msg); pump(); }
async function pump() {
  if (processing) return;
  processing = true;
  while (msgQueue.length) {
    const msg = msgQueue.shift();
    await handle(msg);
  }
  processing = false;
}

async function handle(msg) {
  if (msg.type === "state") {
    App.state = msg;
    await ensureScenario(msg.scenario);
    applyState(msg);
  } else if (msg.type === "history") {
    App.history = msg.data || [];
    renderChart();
  } else if (msg.type === "frame") {
    App.lastFrame = msg;
    renderFrame(msg);
  }
}

async function ensureScenario(id) {
  if (App.scenarioData[id]) return;
  const r = await fetch(`/web/scenarios/${id}.json`);
  App.scenarioData[id] = await r.json();
}

function applyState(s) {
  if (window.Sidebar) Sidebar.onState(s);
  // Map picker.
  const mp = document.getElementById("map-picker");
  if (mp.options.length !== (s.scenarios || []).length) {
    mp.innerHTML = "";
    for (const sc of s.scenarios) {
      const o = document.createElement("option");
      o.value = sc.id; o.textContent = sc.title; mp.appendChild(o);
    }
  }
  mp.value = s.scenario;

  // Panes.
  const curPanes = Object.keys(App.panes);
  if (curPanes.join() !== s.panes.join()) rebuildPanes(s.panes);
  else {
    const data = App.scenarioData[s.scenario];
    for (const p of Object.values(App.panes)) if (p.mapData !== data) p.setMap(data);
  }

  // Play/speed/clock/rush.
  document.getElementById("btn-play").textContent = s.playing ? "⏸" : "▶";
  const sps = s.sps; const speed = sps / 10;
  document.getElementById("speed").value = speed;
  document.getElementById("speed-label").textContent = speed + "×";
  App.inspected = s.inspected;
  updateRush(s.t, s.meta && s.meta.rush);
  updateClock(s.t);

  // ask= URL option -> prefill copilot (handled by sidebar step).
}

function renderFrame(frame) {
  updateClock(frame.t);
  updateRush(frame.t, App.state && App.state.meta && App.state.meta.rush);
  // heat scale shared across panes.
  if (App.heatOn) computeHeatMax(frame);
  for (const [strat, sim] of Object.entries(frame.sims)) {
    const p = App.panes[strat];
    if (p) p.render(sim);
  }
  // events -> toasts
  for (const ev of (frame.events || [])) {
    toast(`${ev.label}`);
    if (window.Sidebar) Sidebar.logEvent(frame.t, ev.label);
  }
  // follow card
  if (frame.follow && window.Sidebar) Sidebar.onFollow(frame.follow);
  pushHistory(frame);
  renderChart();
  if (window.Sidebar) Sidebar.onFrame(frame);
}

function computeHeatMax(frame) {
  let mx = 0.001;
  for (const sim of Object.values(frame.sims)) {
    for (const v of Object.values(sim.heat || {})) mx = Math.max(mx, v);
  }
  // smooth the shared scale
  App.heatMax = App.heatMax * 0.9 + mx * 0.1;
}

// ======================================================================
// Chart strip: cumulative idle CO2 (solid) + 90s rolling-avg queue (dashed)
// ======================================================================
function pushHistory(frame) {
  const snap = {};
  for (const [strat, sim] of Object.entries(frame.sims)) {
    snap[strat] = [sim.m.idle_co2_kg, sim.m.queue];
  }
  App.history.push([frame.t, snap]);
  if (App.history.length > 2600) App.history.shift();
}

function renderChart() {
  const panes = Object.keys(App.panes);
  if (!panes.length) return;
  const labels = App.history.map(h => h[0]);
  const datasets = [];
  for (const strat of panes) {
    const idle = App.history.map(h => (h[1][strat] ? h[1][strat][0] : null));
    const queueRaw = App.history.map(h => (h[1][strat] ? h[1][strat][1] : null));
    const queueAvg = rollingAvg(queueRaw, 90);
    datasets.push({
      label: (App.state.labels[strat] || strat) + " idle CO₂",
      data: idle, borderColor: COLORS[strat], borderWidth: 1.6,
      pointRadius: 0, yAxisID: "y", tension: 0.2,
    });
    datasets.push({
      label: (App.state.labels[strat] || strat) + " queue",
      data: queueAvg, borderColor: COLORS[strat], borderWidth: 1.2,
      borderDash: [4, 3], pointRadius: 0, yAxisID: "y2", tension: 0.2,
    });
  }

  const rush = App.state.meta && App.state.meta.rush;
  if (!App.chart) {
    const ctx = document.getElementById("chart").getContext("2d");
    App.chart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        scales: {
          x: { display: false },
          y: { position: "left", title: { display: true, text: "idle CO₂ kg" },
               ticks: { color: "#8b98ad" }, grid: { color: "#1a2130" } },
          y2: { position: "right", title: { display: true, text: "queue" },
                ticks: { color: "#8b98ad" }, grid: { display: false } },
        },
        plugins: {
          legend: { labels: { color: "#8b98ad", boxWidth: 10, font: { size: 9 } } },
          annotation: undefined,
        },
      },
    });
  } else {
    App.chart.data.labels = labels;
    App.chart.data.datasets = datasets;
    App.chart.update("none");
  }
  drawRushShade(rush);
}

function drawRushShade(rush) {
  // Lightweight: paint a translucent band behind the canvas via CSS gradient.
  if (!rush || !App.history.length) return;
  const strip = document.getElementById("chartstrip");
  const tMax = App.history[App.history.length - 1][0] || 1;
  const tMin = App.history[0][0] || 0;
  const span = Math.max(1, tMax - tMin);
  const a = Math.max(0, (rush[0] - tMin) / span) * 100;
  const b = Math.min(100, (rush[1] - tMin) / span) * 100;
  strip.style.background =
    `linear-gradient(90deg, var(--panel) ${a}%, var(--rush) ${a}%, ` +
    `var(--rush) ${b}%, var(--panel) ${b}%)`;
}

function rollingAvg(arr, win) {
  const out = new Array(arr.length).fill(null);
  let sum = 0, cnt = 0; const q = [];
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i];
    q.push(v);
    if (v != null) { sum += v; cnt++; }
    if (q.length > win) {
      const old = q.shift();
      if (old != null) { sum -= old; cnt--; }
    }
    out[i] = cnt ? sum / cnt : null;
  }
  return out;
}

// ======================================================================
// Top bar helpers / controls
// ======================================================================
function setConn(ok) { document.getElementById("conn").classList.toggle("ok", ok); }
function updateClock(t) {
  const m = Math.floor(t / 60), s = t % 60;
  document.getElementById("clock").textContent = `${m}:${String(s).padStart(2, "0")}`;
}
function updateRush(t, rush) {
  const pill = document.getElementById("rush-pill");
  if (rush && t >= rush[0] && t < rush[1]) {
    pill.textContent = "RUSH HOUR"; pill.classList.add("rush");
  } else { pill.textContent = "off-peak"; pill.classList.remove("rush"); }
}

function send(msg) {
  if (App.ws && App.ws.readyState === WebSocket.OPEN) App.ws.send(JSON.stringify(msg));
}
function sendFollow(vid) { App.followed = vid; send({ type: "follow", vid }); }
function sendInspect(tls) { App.inspected = tls; send({ type: "inspect", tls }); }

function toast(text) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = text;
  document.getElementById("toasts").appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

function wireControls() {
  document.getElementById("btn-play").onclick = () => {
    const playing = App.state && App.state.playing;
    send({ type: "control", action: playing ? "pause" : "play" });
  };
  document.getElementById("btn-reset").onclick = () =>
    send({ type: "control", action: "reset" });
  document.getElementById("map-picker").onchange = (e) =>
    send({ type: "config", scenario: e.target.value });
  document.getElementById("compare-picker").onchange = (e) =>
    send({ type: "config", panes: paneSetFor(e.target.value) });
  document.getElementById("speed").oninput = (e) => {
    const sps = Math.round(parseFloat(e.target.value) * 10);
    document.getElementById("speed-label").textContent = (sps / 10) + "×";
    send({ type: "speed", sps });
  };
  document.getElementById("btn-heat").onclick = () => toggleHeat();

  window.addEventListener("resize", () => {
    for (const p of Object.values(App.panes)) p.resize();
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
    if (e.code === "Space") { e.preventDefault();
      send({ type: "control", action: App.state.playing ? "pause" : "play" }); }
    else if (e.key === "a" || e.key === "A") send({ type: "event", event: "ambulance" });
    else if (e.key === "s" || e.key === "S") send({ type: "event", event: "surge" });
    else if (e.key === "c" || e.key === "C") send({ type: "event", event: "crash" });
    else if (e.key === "h" || e.key === "H") toggleHeat();
    else if (e.key === "Escape") { App.followed = null; App.inspected = null;
      send({ type: "follow", vid: null }); send({ type: "inspect", tls: null }); }
  });
}

function toggleHeat() {
  App.heatOn = !App.heatOn;
  document.getElementById("btn-heat").classList.toggle("active", App.heatOn);
}

// ======================================================================
// Boot
// ======================================================================
function applyPopoutView() {
  // ?view=<tab> shows only that sidebar panel at a fixed size.
  const view = URL_OPTS.view;
  if (!view) return;
  document.body.classList.add("popout");
  if (window.Sidebar) Sidebar.showOnly(view);
  try { window.resizeTo(560, 860); } catch (e) {}
}

// ---- replay mode: play a recorded .jsonl instead of a live socket ----
const Replay = { frames: [], idx: 0, playing: false, timer: null };

async function startReplay(path) {
  const r = await fetch(path);
  const text = await r.text();
  const lines = text.trim().split("\n");
  const msgs = lines.map(l => JSON.parse(l));
  // First line is the state message; rest are frames.
  Replay.frames = msgs.filter(m => m.type === "frame");
  const stateMsg = msgs.find(m => m.type === "state");
  setConn(true);
  // Replay has no server; drive the handlers directly, in order.
  if (stateMsg) { stateMsg.playing = false; await handle(stateMsg); }

  // Seek to ?t=
  if (URL_OPTS.t != null) {
    Replay.idx = Math.max(0, Replay.frames.findIndex(f => f.t >= URL_OPTS.t));
    if (Replay.idx < 0) Replay.idx = 0;
    // Prime the view with the sought frame.
    if (Replay.frames[Replay.idx]) await handle(Replay.frames[Replay.idx]);
  }

  // Override controls for local playback.
  document.getElementById("btn-play").onclick = toggleReplay;
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space") { e.preventDefault(); toggleReplay(); }
  });

  if (URL_OPTS.autoplay) { Replay.playing = true; tickReplay(); }
  document.getElementById("btn-play").textContent = Replay.playing ? "⏸" : "▶";
}

function toggleReplay() {
  Replay.playing = !Replay.playing;
  document.getElementById("btn-play").textContent = Replay.playing ? "⏸" : "▶";
  if (Replay.playing) tickReplay();
}

function tickReplay() {
  if (!Replay.playing) return;
  if (Replay.idx >= Replay.frames.length) { Replay.playing = false; return; }
  const frame = Replay.frames[Replay.idx++];
  handle(frame);
  const sps = (App.state && App.state.sps) || 10;
  Replay.timer = setTimeout(tickReplay, 1000 / sps);
}

function boot() {
  if (URL_OPTS.heat) document.getElementById("btn-heat").classList.add("active");
  wireControls();
  if (window.Sidebar) Sidebar.init();
  applyPopoutView();
  if (URL_OPTS.replay) {
    startReplay(URL_OPTS.replay);
  } else {
    connect();
  }
}
document.addEventListener("DOMContentLoaded", boot);

// Expose a few helpers for the sidebar module.
window.CS = { send, toast, get App() { return App; }, COLORS, URL_OPTS,
  sendFollow, sendInspect };
