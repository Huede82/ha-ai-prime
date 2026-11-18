// Table editor: one row per point (time + 5 channel inputs), add/delete row.
//
// Props: points, readonly, lang. Events (bubbling, composed): points-change {points}.
// Invalid input (bad time text, empty/non-numeric value) is stored as an invalid
// number (-1 / NaN) so model.validate flags it; the raw text stays visible.

import { translator } from "./i18n.js";
import { CHANNEL_ORDER, CHANNEL_COLORS, deletePoint, fmtTime, insertPoint, parseTime } from "./model.js";

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const STYLE = `
  :host { display: block; color: var(--primary-text-color, #212121); }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 4px; text-align: left; font-size: 13px; }
  th { color: var(--secondary-text-color, #727272); font-weight: 500; white-space: nowrap; }
  .sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
  input { padding: 6px; font: inherit; border-radius: 8px; min-width: 0; box-sizing: border-box;
    color: var(--primary-text-color, #212121); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ccc); }
  input.time { width: 5.5em; }
  input.val { width: 5em; }
  tr.bad input { border-color: var(--error-color, #db4437); border-width: 2px; }
  button { cursor: pointer; border: 0; border-radius: 8px; padding: 8px 12px; font: inherit; min-height: 36px;
    background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
  button:disabled { opacity: .5; cursor: default; }
  .bar { margin-top: 8px; }
`;

export class AiPrimeTableEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._points = [];
    this._raw = {}; // row index -> raw time text kept while it does not parse
    this._readonly = false;
    this._lang = null;
    this._bad = null;
  }

  set points(p) {
    this._points = p.map((x) => ({ t: x.t, v: { ...x.v } }));
    this._raw = {};
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

  setError(index) {
    this._bad = index;
    this.shadowRoot.querySelectorAll("tbody tr").forEach((tr) => tr.classList.toggle("bad", Number(tr.dataset.i) === index));
  }

  connectedCallback() {
    this._render();
  }

  _emit() {
    this.dispatchEvent(new CustomEvent("points-change", { detail: { points: this._points }, bubbles: true, composed: true }));
  }

  _render() {
    const t = translator(this._lang);
    const dis = this._readonly ? " disabled" : "";
    const rows = this._points.map((p, i) => {
      const time = this._raw[i] != null ? this._raw[i] : fmtTime(p.t);
      const cells = CHANNEL_ORDER.map((c) => {
        const v = Number.isFinite(p.v[c]) ? p.v[c] : "";
        return `<td><input class="val" type="number" min="0" max="100" step="0.1" data-c="${c}" value="${esc(v)}" aria-label="${esc(t(c))}"${dis}></td>`;
      }).join("");
      return `<tr data-i="${i}"${i === this._bad ? ' class="bad"' : ""}>
        <td><input class="time" type="text" inputmode="numeric" value="${esc(time)}" aria-label="${esc(t("time"))}"${dis}></td>${cells}
        <td>${this._readonly ? "" : `<button data-del="${i}" aria-label="${esc(t("delete_point"))}"${this._points.length <= 2 ? " disabled" : ""}>&#128465;</button>`}</td></tr>`;
    }).join("");
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <div class="scroll"><table>
        <thead><tr><th>${esc(t("time"))}</th>${CHANNEL_ORDER.map((c) => `<th><span class="sw" style="background:${CHANNEL_COLORS[c]}"></span>${esc(t(c))}</th>`).join("")}<th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${this._readonly ? "" : `<div class="bar"><button id="add">${esc(t("add_row"))}</button></div>`}`;
    for (const tr of this.shadowRoot.querySelectorAll("tbody tr")) {
      const i = Number(tr.dataset.i);
      tr.querySelector(".time").addEventListener("change", (ev) => {
        const m = parseTime(ev.target.value);
        if (m == null) {
          this._raw[i] = ev.target.value;
          this._points[i].t = -1;
        } else {
          delete this._raw[i];
          this._points[i].t = m;
        }
        this._emit();
      });
      for (const input of tr.querySelectorAll(".val")) {
        input.addEventListener("change", () => {
          this._points[i].v[input.dataset.c] = input.value === "" ? NaN : Number(input.value);
          this._emit();
        });
      }
    }
    for (const b of this.shadowRoot.querySelectorAll("[data-del]")) {
      b.addEventListener("click", () => {
        this._points = deletePoint(this._points, Number(b.dataset.del));
        this._raw = {};
        this._render();
        this._emit();
      });
    }
    const add = this.shadowRoot.getElementById("add");
    if (add) {
      add.addEventListener("click", () => {
        const last = this._points.length ? this._points[this._points.length - 1].t : 0;
        let tt = last + 30 <= 1440 ? last + 30 : 720;
        while (this._points.some((p) => p.t === tt) && tt < 1440) tt += 5;
        if (this._points.some((p) => p.t === tt)) return;
        this._points = insertPoint(this._points, tt);
        this._raw = {};
        this._render();
        this._emit();
      });
    }
  }
}

if (!customElements.get("ai-prime-table-editor")) customElements.define("ai-prime-table-editor", AiPrimeTableEditor);
