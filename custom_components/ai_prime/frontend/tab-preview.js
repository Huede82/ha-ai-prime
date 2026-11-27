// Studio tab "Vorschau": light settings, one day of the active schedule (thin) and the lamp
// output (thick: theory or with weather) per channel, sun/moon markers, now-line for today,
// a play cursor (2 s per hour) with live readout and a times list; optionally mirrored on the lamp.

import { errorText, translator } from "./i18n.js";
import { CHANNEL_COLORS, CHANNEL_ORDER, curveAt, fmtTime, mixColor, nowMinutes, todayIso } from "./model.js";

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const errText = (e) => (e && (e.message || e.code)) || String(e);

const W = 800;
const H = 300;
const ML = 34; // left margin (y labels)
const MT = 34; // top margin (marker labels)
const MB = 22; // bottom margin (hour labels)
const PW = W - ML - 8;
const PH = H - MT - MB;
const SEC_PER_HOUR = 2; // playback speed: a whole day takes 24 * SEC_PER_HOUR = 48 s
const MIN_PER_SEC = 60 / SEC_PER_HOUR;
const DEMO_INTERVAL_MS = 100; // at most one lamp frame per interval
const DEMO_IDLE_MS = 5000; // scrubbing: hand the lamp back after this long without input
const SERIES_KEY = "ai_prime_preview_series";
const LAMP_KEY = "ai_prime_preview_on_lamp";
const LIGHT_DEFAULT = { brightness: 100, night_light: 0, moon_max: 0 };

const STYLE = `
  :host { display: block; max-width: 960px; margin: 0 auto; color: var(--primary-text-color, #212121); }
  .bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
  button { cursor: pointer; border: 0; border-radius: 8px; padding: 8px 16px; font: inherit;
    background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
  button.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  input[type=date] { padding: 7px; font: inherit; border-radius: 8px; min-width: 0;
    color: var(--primary-text-color, #212121); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ccc); }
  .chart { padding: 8px; border-radius: 12px; background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ddd); }
  svg { display: block; width: 100%; height: auto; touch-action: pan-y; }
  svg .cursor { stroke: var(--primary-color, #03a9f4); stroke-width: 2; pointer-events: none; }
  svg text { fill: var(--secondary-text-color, #727272); font-size: 11px; }
  svg .grid { stroke: var(--divider-color, #e0e0e0); stroke-width: 1; }
  svg .mk { stroke: var(--secondary-text-color, #727272); stroke-width: 1; stroke-dasharray: 2 3; }
  svg .now { stroke: var(--primary-text-color, #212121); stroke-width: 1.5; stroke-dasharray: 5 3; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; margin-top: 6px;
    color: var(--secondary-text-color, #727272); }
  .legend i { display: inline-block; width: 12px; height: 3px; margin-right: 4px; vertical-align: middle; }
  .legend .thin { opacity: .6; }
  [hidden] { display: none !important; }
  .grp { display: flex; gap: 8px; }
  .play { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-top: 10px; }
  .readout { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; margin-top: 8px; font-size: 13px;
    font-variant-numeric: tabular-nums; }
  .readout .time { font-size: 18px; font-weight: 500; }
  .readout .sw { width: 28px; height: 28px; border-radius: 50%; flex: none; background: #333;
    border: 1px solid var(--divider-color, #ccc); }
  .readout .ch { display: inline-flex; align-items: center; gap: 4px; }
  .readout .ch i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .light { margin-bottom: 12px; padding: 12px 16px; border-radius: 12px; background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ddd); }
  .light h3 { margin: 0 0 8px; font-size: 16px; font-weight: 500; }
  .row { margin-bottom: 10px; }
  .row .top { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
  .row .val { color: var(--secondary-text-color, #727272); font-variant-numeric: tabular-nums; }
  input[type=range] { display: block; width: 100%; box-sizing: border-box; margin: 4px 0 0;
    accent-color: var(--primary-color, #03a9f4); }
  .times { margin-top: 12px; padding: 12px 16px; border-radius: 12px; font-size: 14px;
    background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #ddd);
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 24px; }
  .times div { display: flex; justify-content: space-between; gap: 12px; }
  .times span:first-child { color: var(--secondary-text-color, #727272); }
  .hint { font-size: 13px; color: var(--secondary-text-color, #727272); margin-top: 8px; }
  .error { color: var(--error-color, #db4437); font-size: 14px; }
`;

function addDays(iso, n) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export class AiPrimeTabPreview extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._entryId = null;
    this._cfg = null;
    this._date = null; // ISO date; null until first load (then today)
    this._preview = null;
    this._error = null;
    this._seq = 0;
    this._series = "effective"; // "theory" | "effective" (thick lines); default = today (with weather)
    try {
      if (localStorage.getItem(SERIES_KEY) === "theory") this._series = "theory";
    } catch (_) {}
    this._onLamp = true; // mirror the playback on the lamp (per viewer)
    try {
      if (localStorage.getItem(LAMP_KEY) === "0") this._onLamp = false;
    } catch (_) {}
    this._demoActive = false; // the backend holds the lamp for us: demo/stop is due
    this._demoCall = null; // promise of the frame call in flight
    this._demoAt = 0; // performance.now() of the last frame sent
    this._demoError = null;
    this._demoIdle = 0;
    this._vals = null; // last readout values (percent per channel)
    this._cursor = null; // play cursor in minutes (null = not used yet)
    this._playing = false;
    this._raf = 0;
    this._last = 0;
    this._light = { ...LIGHT_DEFAULT };
    this._saving = false;
    this._lightError = null;
  }

  set hass(hass) {
    this._hass = hass;
  }

  get hass() {
    return this._hass;
  }

  set entryId(value) {
    this._entryId = value;
  }

  get entryId() {
    return this._entryId;
  }

  set cfg(cfg) {
    const changed = !!this._cfg && (this._cfg.active !== (cfg && cfg.active) || this._cfg.follow !== (cfg && cfg.follow));
    this._cfg = cfg;
    if (cfg && cfg.light && !this._saving) {
      this._light = { ...LIGHT_DEFAULT, ...cfg.light };
      if (this.isConnected && this._preview) this._render();
    }
    if (changed) this._load();
  }

  get cfg() {
    return this._cfg;
  }

  // Read-only tab: never dirty.
  isDirty() {
    return false;
  }

  canLeave() {
    return true;
  }

  discard() {}

  connectedCallback() {
    if (this._cfg && this._cfg.light) this._light = { ...LIGHT_DEFAULT, ...this._cfg.light };
    this._render();
    this._load();
  }

  disconnectedCallback() {
    this._stop();
  }

  _isAdmin() {
    return !(this._hass && this._hass.user && this._hass.user.is_admin === false);
  }

  // ---- playback ----

  _stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = 0;
    this._playing = false;
    this._demoStop();
    this._syncPlay();
  }

  _demoOn() {
    return this._onLamp && this._isAdmin();
  }

  /** Send the current readout to the lamp; dropped while the previous call is pending. */
  _sendFrame(force) {
    if (!this._demoOn() || !this._vals || !this._hass || this._demoCall) return;
    const now = performance.now();
    if (!force && now - this._demoAt < DEMO_INTERVAL_MS) return;
    this._demoAt = now;
    const values = {};
    for (const c of CHANNEL_ORDER) values[c] = Math.round(this._vals[c] * 10) / 10;
    this._demoActive = true;
    this._demoError = null;
    const call = this._hass.callWS({ type: "ai_prime/demo/frame", entry_id: this._entryId, values }).then(
      () => {},
      (err) => {
        this._demoActive = false;
        this._demoError = errorText(err, translator(this._hass && this._hass.language));
        if (this._playing) this._stop();
        else this._syncPlay();
      },
    );
    this._demoCall = call;
    call.then(() => {
      if (this._demoCall === call) this._demoCall = null;
      this._syncDemo();
    });
    this._syncDemo();
  }

  /** End the lamp demo once (after a frame still in flight, so stop is the last word). */
  _demoStop() {
    clearTimeout(this._demoIdle);
    this._demoIdle = 0;
    if (!this._demoActive) return;
    this._demoActive = false;
    const pending = this._demoCall || Promise.resolve();
    pending.then(() => {
      if (this._demoActive || !this._hass) return; // a new demo started meanwhile
      return this._hass.callWS({ type: "ai_prime/demo/stop", entry_id: this._entryId }).catch(() => {});
    });
    this._syncDemo();
  }

  _toggleLamp() {
    if (!this._isAdmin()) return;
    this._onLamp = !this._onLamp;
    try {
      localStorage.setItem(LAMP_KEY, this._onLamp ? "1" : "0");
    } catch (_) {}
    if (!this._onLamp) this._demoStop();
    this._demoError = null;
    this._syncDemo();
  }

  _syncDemo() {
    const root = this.shadowRoot;
    if (!root) return;
    const btn = root.getElementById("lamp");
    if (btn) {
      btn.className = this._onLamp && this._isAdmin() ? "on" : "";
      btn.disabled = !this._isAdmin();
      btn.setAttribute("aria-pressed", String(this._onLamp));
    }
    const hint = root.getElementById("demo-hint");
    if (hint) hint.hidden = !this._demoActive;
    const err = root.getElementById("demo-error");
    if (err) {
      const t = translator(this._hass && this._hass.language);
      err.hidden = !this._demoError;
      err.textContent = this._demoError ? `${t("error_prefix")}: ${this._demoError}` : "";
    }
  }

  _togglePlay() {
    if (this._playing) {
      this._stop();
      return;
    }
    if (!this._preview) return;
    if (this._cursor === null || this._cursor >= 1440) this._cursor = 0;
    this._playing = true;
    this._demoError = null;
    this._last = 0;
    this._raf = requestAnimationFrame((ts) => this._tick(ts));
    this._syncPlay();
    this._updateCursor();
  }

  _tick(ts) {
    if (!this._playing) return;
    if (this._last) this._cursor = Math.min(1440, this._cursor + ((ts - this._last) / 1000) * MIN_PER_SEC);
    this._last = ts;
    this._updateCursor();
    this._sendFrame(false);
    if (this._cursor >= 1440) this._stop();
    else this._raf = requestAnimationFrame((t2) => this._tick(t2));
  }

  _syncPlay() {
    const btn = this.shadowRoot && this.shadowRoot.getElementById("play");
    if (!btn) return;
    const t = translator(this._hass && this._hass.language);
    btn.textContent = t(this._playing ? "pv_pause" : "pv_play");
  }

  _seriesData() {
    const c = this._preview && this._preview.curve;
    return c && (c[this._series] || c.effective);
  }

  /** Move the cursor line and refresh the readout without re-rendering the chart. */
  _updateCursor() {
    const root = this.shadowRoot;
    const p = this._preview;
    const line = root.getElementById("cursor");
    const out = root.getElementById("readout");
    if (!p || !line || !out || this._cursor === null) return;
    const m = Math.min(1440, Math.max(0, this._cursor));
    const xx = (ML + (m / 1440) * PW).toFixed(1);
    line.setAttribute("x1", xx);
    line.setAttribute("x2", xx);
    line.removeAttribute("visibility");
    const vals = curveAt(p.curve, this._seriesData(), m);
    this._vals = vals;
    const t = translator(this._hass && this._hass.language);
    root.getElementById("ro-time").textContent = fmtTime(m);
    root.getElementById("ro-swatch").style.background = mixColor(vals).css;
    for (const c of CHANNEL_ORDER) root.getElementById(`ro-${c}`).textContent = `${(Math.round(vals[c] * 10) / 10).toFixed(1)} %`;
    out.hidden = false;
    out.setAttribute("aria-label", t("pv_readout"));
  }

  _seek(ev) {
    const svg = this.shadowRoot.getElementById("chart");
    if (!svg) return;
    const r = svg.getBoundingClientRect();
    if (!r.width) return;
    const vx = ((ev.clientX - r.left) / r.width) * W;
    this._cursor = Math.min(1440, Math.max(0, ((vx - ML) / PW) * 1440));
    this._last = 0;
    this._updateCursor();
    if (!this._playing && this._demoOn()) {
      // Scrubbing while paused: the lamp follows; hand it back when input stops.
      this._sendFrame(false);
      clearTimeout(this._demoIdle);
      this._demoIdle = setTimeout(() => this._demoStop(), DEMO_IDLE_MS);
    }
  }

  _setSeries(series) {
    if (series === this._series) return;
    this._stop();
    this._series = series;
    try {
      localStorage.setItem(SERIES_KEY, series);
    } catch (_) {}
    this._render();
  }

  // ---- light settings ----

  async _saveLight(key, value) {
    if (this._saving || !this._isAdmin()) return;
    this._saving = true;
    this._lightError = null;
    this._renderLight();
    try {
      const res = await this._hass.callWS({ type: "ai_prime/light/set", entry_id: this._entryId, [key]: value });
      if (res) this._light = { ...this._light, ...res };
    } catch (err) {
      // lamp_rejected: stored in HA, only the lamp refused it.
      this._lightError = errorText(err, translator(this._hass && this._hass.language));
    }
    this._saving = false;
    this._stop();
    this.dispatchEvent(new CustomEvent("studio-reload", { bubbles: true, composed: true }));
    await this._load();
    this._renderLight();
  }

  _lightRows(t) {
    const l = this._light;
    const dis = this._saving || !this._isAdmin() ? " disabled" : "";
    const nightTxt = l.night_light > 0 ? `${Number(l.night_light).toFixed(1)} %` : t("light_off");
    return `<h3>${esc(t("light_title"))}</h3>
      <div class="row"><div class="top"><label for="l-brightness">${esc(t("light_brightness"))}</label><span class="val" id="v-brightness">${l.brightness} %</span></div>
        <input type="range" id="l-brightness" data-key="brightness" min="1" max="100" step="1" value="${l.brightness}"${dis}></div>
      <div class="row"><div class="top"><label for="l-night_light">${esc(t("light_night"))}</label><span class="val" id="v-night_light">${esc(nightTxt)}</span></div>
        <input type="range" id="l-night_light" data-key="night_light" min="0" max="5" step="0.1" value="${l.night_light}"${dis}>
        <div class="hint" style="margin-top:2px">${esc(t("light_night_hint"))}</div></div>
      <div class="row"><div class="top"><label for="l-moon_max">${esc(t("light_moon"))}</label><span class="val" id="v-moon_max">${Number(l.moon_max).toFixed(1)} %</span></div>
        <input type="range" id="l-moon_max" data-key="moon_max" min="0" max="5" step="0.1" value="${l.moon_max}"${dis}></div>
      ${this._saving ? `<div class="hint" id="l-saving">${esc(t("light_saving"))}</div>` : ""}
      ${this._lightError ? `<div class="error" id="l-error">${esc(t("error_prefix"))}: ${esc(this._lightError)}</div>` : ""}`;
  }

  _wireLight() {
    const t = translator(this._hass && this._hass.language);
    for (const input of this.shadowRoot.querySelectorAll("#light input[type=range]")) {
      const key = input.dataset.key;
      input.addEventListener("input", () => {
        const v = Number(input.value);
        const lab = this.shadowRoot.getElementById(`v-${key}`);
        lab.textContent = key === "brightness" ? `${v} %` : key === "night_light" && v === 0 ? t("light_off") : `${v.toFixed(1)} %`;
      });
      input.addEventListener("change", () => {
        const v = Number(input.value);
        this._light = { ...this._light, [key]: key === "brightness" ? Math.round(v) : Math.round(v * 10) / 10 };
        this._saveLight(key, this._light[key]);
      });
    }
  }

  _renderLight() {
    const box = this.shadowRoot.getElementById("light");
    if (!box) return;
    box.innerHTML = this._lightRows(translator(this._hass && this._hass.language));
    this._wireLight();
  }

  _tz() {
    return this._hass && this._hass.config && this._hass.config.time_zone;
  }

  _today() {
    return todayIso(this._tz());
  }

  async _load() {
    if (!this._hass || !this._entryId) return;
    if (!this._date) this._date = this._today();
    const seq = ++this._seq;
    try {
      const res = await this._hass.callWS({ type: "ai_prime/preview", entry_id: this._entryId, date: this._date });
      if (seq !== this._seq) return;
      this._preview = res;
      this._error = null;
    } catch (err) {
      if (seq !== this._seq) return;
      this._error = err;
    }
    this._render();
  }

  _setDate(iso) {
    if (!iso || iso === this._date) return;
    this._stop();
    this._date = iso;
    this._preview = null;
    this._render();
    this._load();
  }

  _chart(p, t) {
    const x = (m) => (ML + (m / 1440) * PW).toFixed(1);
    const y = (v) => (MT + PH - (Math.min(100, Math.max(0, v)) / 100) * PH).toFixed(1);
    let svg = "";
    for (const g of [0, 25, 50, 75, 100]) {
      svg += `<line class="grid" x1="${ML}" x2="${ML + PW}" y1="${y(g)}" y2="${y(g)}"/><text x="${ML - 4}" y="${Number(y(g)) + 4}" text-anchor="end">${g}</text>`;
    }
    for (let h = 0; h <= 24; h += 3) {
      svg += `<text x="${x(h * 60)}" y="${H - 6}" text-anchor="${h === 0 ? "start" : h === 24 ? "end" : "middle"}">${String(h).padStart(2, "0")}:00</text>`;
    }
    const curve = p.curve;
    const line = (series, width, opacity, cls) =>
      CHANNEL_ORDER.map((c) => {
        const pts = (series[c] || []).map((v, i) => `${x(curve.t[i])},${y(v)}`).join(" ");
        return `<polyline class="${cls}" fill="none" stroke="${CHANNEL_COLORS[c]}" stroke-width="${width}" stroke-opacity="${opacity}" stroke-linejoin="round" points="${pts}"/>`;
      }).join("");
    svg += line(curve.base, 1, 0.7, "base") + line(this._seriesData(), 2.8, 1, "eff");
    (p.markers || []).forEach((m, i) => {
      const lane = i % 2; // alternate rows so neighbouring labels do not collide
      const anchor = m.t > 1200 ? "end" : m.t < 240 ? "start" : "middle";
      svg += `<line class="mk" data-kind="${esc(m.kind)}" x1="${x(m.t)}" x2="${x(m.t)}" y1="${MT - 4}" y2="${MT + PH}"/>`;
      svg += `<text class="mk-label" x="${x(m.t)}" y="${12 + lane * 13}" text-anchor="${anchor}">${esc(t(`marker_${m.kind}`))} ${fmtTime(m.t)}</text>`;
    });
    if (p.date === this._today()) {
      const n = nowMinutes(this._tz());
      svg += `<line class="now" x1="${x(n)}" x2="${x(n)}" y1="${MT - 4}" y2="${MT + PH}"><title>${esc(t("now"))} ${fmtTime(n)}</title></line>`;
    }
    svg += `<line id="cursor" class="cursor" visibility="hidden" x1="${ML}" x2="${ML}" y1="${MT - 4}" y2="${MT + PH}"/>`;
    return svg;
  }

  _times(p, t) {
    const rows = [];
    const row = (k, v) => rows.push(`<div><span>${esc(k)}</span><span>${esc(v)}</span></div>`);
    if (p.sun) {
      if (p.sun.local) row(`${t("info_local")} (${p.sun.local.tz})`, `${p.sun.local.rise} / ${p.sun.local.set}`);
      if (p.sun.home) row(`${t("info_home")} ${t("sunrise")} / ${t("sunset")}`, `${fmtTime(p.sun.home[0])} / ${fmtTime(p.sun.home[1])}`);
      if (p.sun.day_length_min != null) row(t("info_day_length"), fmtTime(p.sun.day_length_min));
    }
    if (p.moon) {
      const iv = (p.moon.intervals || []).map(([a, b]) => `${fmtTime(a)}–${fmtTime(b)}`).join(", ");
      row(t("pv_moon_up"), iv || "–");
      row(t("info_phase"), `${p.moon.phase} %`);
    }
    if (p.weather) {
      row(t("info_clouds"), `${p.weather.cloud_cover} %`);
      row(t("info_thunder"), t(p.weather.thunderstorm ? "yes" : "no"));
    }
    return rows.join("");
  }

  _render() {
    const root = this.shadowRoot;
    const t = translator(this._hass && this._hass.language);
    const today = this._today();
    const date = this._date || today;
    const mode = date === today ? "today" : date === addDays(today, 1) ? "tomorrow" : "custom";
    const p = this._preview;
    let body = "";
    if (this._error) body = `<div class="error" id="pv-error">${esc(t("error_prefix"))}: ${esc(errText(this._error))}</div>`;
    else if (!p) body = `<div class="hint">${esc(t("loading"))}</div>`;
    else {
      const noPreset = CHANNEL_ORDER.every((c) => (p.curve.effective[c] || []).every((v) => v === 0)) && !(this._cfg && this._cfg.effective_active);
      body = `${noPreset ? `<div class="hint">${esc(t("pv_no_preset"))}</div>` : ""}
        <div class="chart"><svg id="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t("pv_chart"))}">${this._chart(p, t)}</svg>
        <div class="legend">${CHANNEL_ORDER.map((c) => `<span><i style="background:${CHANNEL_COLORS[c]}"></i>${esc(t(c))}</span>`).join("")}
          <span><i class="thin" style="background:var(--secondary-text-color,#727272);height:1px"></i>${esc(t("pv_base"))}</span>
          <span><i style="background:var(--secondary-text-color,#727272);height:3px"></i>${esc(t(this._series === "theory" ? "pv_out_theory" : "pv_out_effective"))}</span></div>
        <div class="play"><button id="play">${esc(t(this._playing ? "pv_pause" : "pv_play"))}</button>
          <button id="lamp" class="${this._onLamp && this._isAdmin() ? "on" : ""}" aria-pressed="${this._onLamp}"${this._isAdmin() ? "" : " disabled"}>${esc(t("pv_on_lamp"))}</button>
          <span class="hint" id="demo-hint"${this._demoActive ? "" : " hidden"}>${esc(t("pv_lamp_hint"))}</span></div>
        <div class="error" id="demo-error"${this._demoError ? "" : " hidden"}>${this._demoError ? `${esc(t("error_prefix"))}: ${esc(this._demoError)}` : ""}</div>
        <div class="readout" id="readout" hidden><span class="sw" id="ro-swatch"></span><span class="time" id="ro-time">00:00</span>
          ${CHANNEL_ORDER.map((c) => `<span class="ch"><i style="background:${CHANNEL_COLORS[c]}"></i>${esc(t(c))} <span id="ro-${c}"></span></span>`).join("")}</div></div>
        <div class="times" id="times">${this._times(p, t)}</div>
        ${p.weather ? "" : `<div class="hint" id="no-weather">${esc(t("pv_no_weather"))}</div>`}`;
    }
    root.innerHTML = `<style>${STYLE}</style>
      <div class="light" id="light">${this._lightRows(t)}</div>
      <div class="bar">
        <button id="d-today" class="${mode === "today" ? "on" : ""}">${esc(t("pv_today"))}</button>
        <button id="d-tomorrow" class="${mode === "tomorrow" ? "on" : ""}">${esc(t("pv_tomorrow"))}</button>
        <input id="d-input" type="date" value="${esc(date)}" aria-label="${esc(t("pv_date"))}">
        <span class="grp"><button id="s-theory" class="${this._series === "theory" ? "on" : ""}">${esc(t("pv_theory"))}</button>
        <button id="s-today" class="${this._series === "effective" ? "on" : ""}">${esc(t("pv_with_weather"))}</button></span>
      </div>${body}`;
    root.getElementById("d-today").addEventListener("click", () => this._setDate(this._today()));
    root.getElementById("d-tomorrow").addEventListener("click", () => this._setDate(addDays(this._today(), 1)));
    root.getElementById("d-input").addEventListener("change", (e) => this._setDate(e.target.value));
    root.getElementById("s-theory").addEventListener("click", () => this._setSeries("theory"));
    root.getElementById("s-today").addEventListener("click", () => this._setSeries("effective"));
    this._wireLight();
    const play = root.getElementById("play");
    if (play) play.addEventListener("click", () => this._togglePlay());
    const lamp = root.getElementById("lamp");
    if (lamp) lamp.addEventListener("click", () => this._toggleLamp());
    const svg = root.getElementById("chart");
    if (svg) {
      let down = false;
      svg.addEventListener("pointerdown", (e) => {
        down = true;
        try {
          svg.setPointerCapture(e.pointerId);
        } catch (_) {}
        this._seek(e);
      });
      svg.addEventListener("pointermove", (e) => down && this._seek(e));
      for (const ev of ["pointerup", "pointercancel"]) {
        svg.addEventListener(ev, () => {
          if (down && !this._playing && this._demoOn()) this._sendFrame(true); // final position
          down = false;
        });
      }
    }
    if (this._cursor !== null) this._updateCursor();
  }
}

if (!customElements.get("ai-prime-tab-preview")) customElements.define("ai-prime-tab-preview", AiPrimeTabPreview);
