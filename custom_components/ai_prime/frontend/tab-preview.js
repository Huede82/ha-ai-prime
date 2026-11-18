// Studio tab "Vorschau": one day of the active schedule (thin) and the effective lamp
// output (thick) per channel, sun/moon markers, now-line for today and a times list.

import { translator } from "./i18n.js";
import { CHANNEL_COLORS, CHANNEL_ORDER, fmtTime, nowMinutes, todayIso } from "./model.js";

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const errText = (e) => (e && (e.message || e.code)) || String(e);

const W = 800;
const H = 300;
const ML = 34; // left margin (y labels)
const MT = 34; // top margin (marker labels)
const MB = 22; // bottom margin (hour labels)
const PW = W - ML - 8;
const PH = H - MT - MB;

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
  svg { display: block; width: 100%; height: auto; }
  svg text { fill: var(--secondary-text-color, #727272); font-size: 11px; }
  svg .grid { stroke: var(--divider-color, #e0e0e0); stroke-width: 1; }
  svg .mk { stroke: var(--secondary-text-color, #727272); stroke-width: 1; stroke-dasharray: 2 3; }
  svg .now { stroke: var(--primary-text-color, #212121); stroke-width: 1.5; stroke-dasharray: 5 3; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; margin-top: 6px;
    color: var(--secondary-text-color, #727272); }
  .legend i { display: inline-block; width: 12px; height: 3px; margin-right: 4px; vertical-align: middle; }
  .legend .thin { opacity: .6; }
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
    this._render();
    this._load();
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
    svg += line(curve.base, 1, 0.7, "base") + line(curve.effective, 2.8, 1, "eff");
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
          <span><i style="background:var(--secondary-text-color,#727272);height:3px"></i>${esc(t("pv_effective"))}</span></div></div>
        <div class="times" id="times">${this._times(p, t)}</div>
        ${p.weather ? "" : `<div class="hint" id="no-weather">${esc(t("pv_no_weather"))}</div>`}`;
    }
    root.innerHTML = `<style>${STYLE}</style>
      <div class="bar">
        <button id="d-today" class="${mode === "today" ? "on" : ""}">${esc(t("pv_today"))}</button>
        <button id="d-tomorrow" class="${mode === "tomorrow" ? "on" : ""}">${esc(t("pv_tomorrow"))}</button>
        <input id="d-input" type="date" value="${esc(date)}" aria-label="${esc(t("pv_date"))}">
      </div>${body}`;
    root.getElementById("d-today").addEventListener("click", () => this._setDate(this._today()));
    root.getElementById("d-tomorrow").addEventListener("click", () => this._setDate(addDays(this._today(), 1)));
    root.getElementById("d-input").addEventListener("change", (e) => this._setDate(e.target.value));
  }
}

if (!customElements.get("ai-prime-tab-preview")) customElements.define("ai-prime-tab-preview", AiPrimeTabPreview);
