// SVG curve editor: 5 channel polylines over 0-24 h x 0-100 %, draggable point
// handles (pointer events), selected point with time field + channel sliders.
//
// Props: points, readonly, selected (index or null). Events (bubbling, composed):
//   points-change {points}  after any edit (also while dragging)
//   select-change {index}   when the selected point changes

import { translator } from "./i18n.js";
import { CHANNEL_ORDER, CHANNEL_COLORS, deletePoint, dragPoint, fmtTime, insertPoint, parseTime } from "./model.js";

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const round1 = (x) => Math.round(x * 10) / 10;
const M = { l: 34, r: 12, t: 12, b: 24 };
const H = 260;
const NS = "http://www.w3.org/2000/svg";

const STYLE = `
  :host { display: block; color: var(--primary-text-color, #212121); }
  [hidden] { display: none !important; }
  .bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
  button { cursor: pointer; border: 0; border-radius: 8px; padding: 8px 16px; font: inherit; min-height: 36px;
    background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
  button:disabled { opacity: .5; cursor: default; }
  svg { display: block; width: 100%; touch-action: none; user-select: none; background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ddd); border-radius: 8px; }
  .grid { stroke: var(--divider-color, #ddd); stroke-width: 1; }
  .lbl { fill: var(--secondary-text-color, #727272); font-size: 11px; }
  polyline { fill: none; stroke-width: 2; stroke-linejoin: round; pointer-events: none; }
  .h { cursor: grab; }
  .h .dot { fill: var(--card-background-color, #fff); stroke: var(--primary-text-color, #212121); stroke-width: 2; pointer-events: none; }
  .h.sel .dot { fill: var(--primary-color, #03a9f4); }
  .h.bad .dot { stroke: var(--error-color, #db4437); stroke-width: 3; }
  .ro .h { cursor: default; }
  .sel-panel { margin-top: 12px; display: grid; grid-template-columns: auto 1fr auto; gap: 6px 10px; align-items: center; }
  .sel-panel .time { grid-column: 1 / -1; display: flex; gap: 8px; align-items: center; }
  input[type=text] { padding: 8px; font: inherit; border-radius: 8px; width: 6em; min-width: 0;
    color: var(--primary-text-color, #212121); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ccc); }
  input[type=range] { width: 100%; min-height: 28px; }
  .sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
  .hint { font-size: 13px; color: var(--secondary-text-color, #727272); margin-top: 8px; }
`;

export class AiPrimeCurveEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._points = [];
    this._readonly = false;
    this._sel = null;
    this._bad = null;
    this._lang = null;
    this._drag = null;
    this._width = 600;
    this._ro = null;
  }

  set points(p) {
    this._points = p.map((x) => ({ t: x.t, v: { ...x.v } }));
    if (this._sel != null && this._sel >= this._points.length) this._sel = null;
    this._render();
  }

  get points() {
    return this._points;
  }

  set readonly(v) {
    this._readonly = !!v;
    if (this.isConnected) this._render();
  }

  set lang(l) {
    this._lang = l;
  }

  set selected(i) {
    this._sel = i == null || i >= this._points.length ? null : i;
    if (this.isConnected) this._render();
  }

  get selected() {
    return this._sel;
  }

  /** Mark point `index` as invalid (null clears). */
  setError(index) {
    this._bad = index;
    this.shadowRoot.querySelectorAll(".h").forEach((g) => g.classList.toggle("bad", Number(g.dataset.i) === index));
  }

  connectedCallback() {
    const w = Math.round(this.getBoundingClientRect().width);
    if (w > 100) this._width = w; // real width before the first build
    this._render();
    if (typeof ResizeObserver !== "undefined") {
      this._ro = new ResizeObserver(() => {
        const w = Math.round(this.getBoundingClientRect().width);
        if (w > 100 && Math.abs(w - this._width) > 1 && !this._drag) {
          this._width = w;
          this._render();
        }
      });
      this._ro.observe(this);
    }
  }

  disconnectedCallback() {
    if (this._ro) this._ro.disconnect();
    this._ro = null;
  }

  _t() {
    return translator(this._lang);
  }

  _emit() {
    this.dispatchEvent(new CustomEvent("points-change", { detail: { points: this._points }, bubbles: true, composed: true }));
  }

  _select(i) {
    if (this._sel === i) return;
    this._sel = i;
    this.dispatchEvent(new CustomEvent("select-change", { detail: { index: i }, bubbles: true, composed: true }));
  }

  // geometry
  _pw() { return this._width - M.l - M.r; }
  _ph() { return H - M.t - M.b; }
  _x(t) { return M.l + (t / 1440) * this._pw(); }
  _y(v) { return M.t + (1 - v / 100) * this._ph(); }
  _max(p) { return Math.max(...CHANNEL_ORDER.map((c) => p.v[c])); }

  _render() {
    const t = this._t();
    const root = this.shadowRoot;
    const ro = this._readonly;
    root.innerHTML = `<style>${STYLE}</style>
      <div class="bar"${ro ? " hidden" : ""}>
        <button id="add" aria-label="${esc(t("add_point"))}" title="${esc(t("add_point"))}">+</button>
        <button id="del" aria-label="${esc(t("delete_point"))}" title="${esc(t("delete_point"))}"${this._sel == null || this._points.length <= 2 ? " disabled" : ""}>&#128465;</button>
      </div>
      <div id="wrap" class="${ro ? "ro" : ""}"></div>
      <div id="panel"></div>`;
    this._buildSvg();
    this._renderPanel();
    if (!ro) {
      root.getElementById("add").addEventListener("click", () => this._add());
      root.getElementById("del").addEventListener("click", () => this._delete());
    }
  }

  _buildSvg() {
    const wrap = this.shadowRoot.getElementById("wrap");
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${this._width} ${H}`);
    svg.setAttribute("height", String(H));
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", this._t()("tab_schedules"));
    const mk = (name, attrs, text) => {
      const el = document.createElementNS(NS, name);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
      if (text != null) el.textContent = text;
      return el;
    };
    for (let v = 0; v <= 100; v += 25) {
      svg.appendChild(mk("line", { class: "grid", x1: M.l, x2: this._width - M.r, y1: this._y(v), y2: this._y(v) }));
      svg.appendChild(mk("text", { class: "lbl", x: M.l - 4, y: this._y(v) + 4, "text-anchor": "end" }, `${v}`));
    }
    for (let h = 0; h <= 24; h += 4) {
      svg.appendChild(mk("line", { class: "grid", x1: this._x(h * 60), x2: this._x(h * 60), y1: M.t, y2: H - M.b }));
      svg.appendChild(mk("text", { class: "lbl", x: this._x(h * 60), y: H - 6, "text-anchor": "middle" }, `${h}`));
    }
    this._lines = {};
    for (const c of CHANNEL_ORDER) {
      const pl = mk("polyline", { stroke: CHANNEL_COLORS[c] });
      this._lines[c] = pl;
      svg.appendChild(pl);
    }
    this._handles = this._points.map((_, i) => {
      const g = mk("g", { class: "h", "data-i": i });
      g.appendChild(mk("circle", { class: "hit", r: 16, fill: "transparent" })); // 32 px hit area
      g.appendChild(mk("circle", { class: "dot", r: 6 }));
      g.addEventListener("pointerdown", (ev) => this._down(ev, i, g));
      g.addEventListener("pointermove", (ev) => this._move(ev, i));
      g.addEventListener("pointerup", (ev) => this._up(ev, g));
      g.addEventListener("pointercancel", (ev) => this._up(ev, g));
      svg.appendChild(g);
      return g;
    });
    wrap.textContent = "";
    wrap.appendChild(svg);
    this._svg = svg;
    this._sync();
  }

  /** Update line/handle geometry in place (keeps pointer capture alive while dragging). */
  _sync() {
    for (const c of CHANNEL_ORDER) {
      this._lines[c].setAttribute("points", this._points.map((p) => `${this._x(p.t)},${this._y(p.v[c])}`).join(" "));
    }
    this._handles.forEach((g, i) => {
      const p = this._points[i];
      if (!p) return;
      g.setAttribute("transform", `translate(${this._x(p.t)},${this._y(this._max(p))})`);
      g.classList.toggle("sel", i === this._sel);
      g.classList.toggle("bad", i === this._bad);
    });
  }

  _down(ev, i, g) {
    ev.preventDefault();
    const wasSel = this._sel === i;
    this._select(i);
    if (this._readonly) {
      this._sync();
      this._renderPanel();
      return;
    }
    g.setPointerCapture(ev.pointerId);
    this._drag = {
      i, start: this._points.map((p) => ({ t: p.t, v: { ...p.v } })), startMax: this._max(this._points[i]),
      x0: ev.clientX, y0: ev.clientY, moved: false,
    };
    this._sync();
    if (!wasSel) {
      this._renderPanel();
      this.shadowRoot.getElementById("del").disabled = this._points.length <= 2;
    }
  }

  _move(ev, i) {
    const d = this._drag;
    if (!d || d.i !== i) return;
    const rect = this._svg.getBoundingClientRect();
    const dxPx = ev.clientX - d.x0;
    const dyPx = ev.clientY - d.y0;
    if (!d.moved && Math.abs(dxPx) <= 4 && Math.abs(dyPx) <= 4) return; // a tap, not a drag
    // Movement since pointerdown, converted to svg units (the svg may be scaled).
    const dx = (dxPx * this._width) / rect.width;
    const dy = (dyPx * H) / rect.height;
    const t = d.start[i].t + (dx * 1440) / this._pw();
    const target = d.startMax - (dy * 100) / this._ph();
    this._points = dragPoint(d.start, i, t, target);
    d.moved = true;
    this._sync();
    this._syncPanel();
    this._emit();
  }

  _up(ev, g) {
    if (!this._drag) return;
    this._drag = null;
    if (g.hasPointerCapture && g.hasPointerCapture(ev.pointerId)) g.releasePointerCapture(ev.pointerId);
  }

  _add() {
    const base = this._sel != null ? this._points[this._sel].t + 30 : 720;
    let t = Math.min(1440, base);
    if (this._points.some((p) => p.t === t)) {
      // Find the nearest free minute after the base, else before it.
      let found = null;
      for (let d = 5; d <= 1440 && found == null; d += 5) {
        for (const c of [t + d, t - d]) if (c >= 0 && c <= 1440 && !this._points.some((p) => p.t === c)) { found = c; break; }
      }
      if (found == null) return;
      t = found;
    }
    this._points = insertPoint(this._points, t);
    this._sel = this._points.findIndex((p) => p.t === t);
    this._render();
    this._emit();
    this.dispatchEvent(new CustomEvent("select-change", { detail: { index: this._sel }, bubbles: true, composed: true }));
  }

  _delete() {
    if (this._sel == null) return;
    this._points = deletePoint(this._points, this._sel);
    this._sel = null;
    this._render();
    this._emit();
    this.dispatchEvent(new CustomEvent("select-change", { detail: { index: null }, bubbles: true, composed: true }));
  }

  _renderPanel() {
    const t = this._t();
    const panel = this.shadowRoot.getElementById("panel");
    const p = this._sel != null ? this._points[this._sel] : null;
    if (!p) {
      panel.innerHTML = `<div class="hint">${esc(t("curve_hint"))}</div>`;
      return;
    }
    const dis = this._readonly ? " disabled" : "";
    panel.innerHTML = `<div class="sel-panel">
      <div class="time"><label for="ptime">${esc(t("time"))}</label>
        <input type="text" id="ptime" value="${esc(fmtTime(p.t))}" inputmode="numeric"${dis}></div>
      ${CHANNEL_ORDER.map((c) => `<label for="s_${c}"><span class="sw" style="background:${CHANNEL_COLORS[c]}"></span>${esc(t(c))}</label>
        <input type="range" id="s_${c}" data-c="${c}" min="0" max="100" step="0.1" value="${p.v[c]}"${dis}>
        <output id="o_${c}">${esc(p.v[c].toFixed(1))}</output>`).join("")}
    </div>`;
    const time = panel.querySelector("#ptime");
    time.addEventListener("change", () => {
      const m = parseTime(time.value);
      const i = this._sel;
      const lo = i > 0 ? this._points[i - 1].t : -1;
      const hi = i < this._points.length - 1 ? this._points[i + 1].t : 1441;
      if (m == null || m <= lo || m >= hi) {
        time.value = fmtTime(this._points[i].t);
        return;
      }
      this._points[i].t = m;
      this._sync();
      this._emit();
    });
    for (const input of panel.querySelectorAll("input[type=range]")) {
      input.addEventListener("input", () => {
        const c = input.dataset.c;
        this._points[this._sel].v[c] = round1(Number(input.value));
        panel.querySelector(`#o_${c}`).textContent = this._points[this._sel].v[c].toFixed(1);
        this._sync();
        this._emit();
      });
    }
  }

  /** Refresh panel values during a drag without rebuilding it. */
  _syncPanel() {
    const panel = this.shadowRoot.getElementById("panel");
    const p = this._sel != null ? this._points[this._sel] : null;
    const time = panel.querySelector("#ptime");
    if (!p || !time) return;
    time.value = fmtTime(p.t);
    for (const c of CHANNEL_ORDER) {
      const r = panel.querySelector(`#s_${c}`);
      if (r) r.value = String(p.v[c]);
      const o = panel.querySelector(`#o_${c}`);
      if (o) o.textContent = p.v[c].toFixed(1);
    }
  }
}

if (!customElements.get("ai-prime-curve-editor")) customElements.define("ai-prime-curve-editor", AiPrimeCurveEditor);
