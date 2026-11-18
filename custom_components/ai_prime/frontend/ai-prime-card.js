// AI Prime Studio card (compact view + card editor). Plain Web Components, no build step.

import "./studio.js";
import { findEntities } from "./entities.js";
import { errorText, langOf, translator } from "./i18n.js";
import { CHANNEL_COLORS, CHANNEL_ORDER, fmtState, fmtTime, nowMinutes } from "./model.js";

const REFRESH_MS = 10 * 60 * 1000;
const SVG_W = 288;
const SVG_H = 72;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const STYLE = `
  :host { display: block; }
  .wrap { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .title { font-size: 20px; flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 12px; padding: 2px 10px; border-radius: 12px; white-space: nowrap;
    background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .chip.warn { background: var(--warning-color, #ffa600); color: var(--text-primary-color, #fff); }
  .row { display: flex; align-items: center; gap: 12px; }
  .row > label { flex: 0 0 96px; color: var(--secondary-text-color); font-size: 14px; }
  .row > .val { flex: 0 0 48px; text-align: right; font-size: 14px; }
  input[type=range] { flex: 1 1 auto; min-width: 0; accent-color: var(--primary-color); }
  select { flex: 1 1 auto; min-width: 0; padding: 8px; font: inherit; border-radius: 8px;
    color: var(--primary-text-color); background: var(--card-background-color);
    border: 1px solid var(--divider-color); }
  svg { width: 100%; height: auto; display: block; background: var(--secondary-background-color); border-radius: 8px; }
  svg .grid { stroke: var(--divider-color); stroke-width: 0.5; }
  svg .now { stroke: var(--primary-text-color); stroke-width: 1; stroke-dasharray: 3 2; }
  svg .mk { stroke: var(--secondary-text-color); stroke-width: 0.6; }
  .times { font-size: 12px; color: var(--secondary-text-color); display: flex; flex-wrap: wrap; gap: 4px 12px; }
  details summary { cursor: pointer; color: var(--secondary-text-color); padding: 4px 0; }
  details .row { margin-top: 6px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; }
  button.primary { cursor: pointer; border: 0; border-radius: 8px; padding: 10px 16px; font: inherit;
    background: var(--primary-color); color: var(--text-primary-color, #fff); align-self: flex-start; }
  .error { padding: 16px; color: var(--error-color); }
  .note { font-size: 12px; color: var(--error-color); }
`;

class AiPrimeCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("ai-prime-card-editor");
  }

  static async getStubConfig(hass) {
    try {
      const entries = await hass.callWS({ type: "ai_prime/entries" });
      const first = entries.find((e) => e.loaded) || entries[0];
      return { entry_id: first ? first.entry_id : "" };
    } catch (_err) {
      return { entry_id: "" };
    }
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._ent = {};
    this._cfg = null; // ai_prime/config/get result
    this._preview = null;
    this._wsError = null;
    this._timer = null;
    this._built = false;
    this._loading = false;
    this._loaded = false;
    this._busy = false;
    this._lang = null;
    this._selCfg = null;
    this._drawnPreview = null;
    this._chipsHtml = null;
    this._studio = null;
  }

  setConfig(config) {
    if (!config || !config.entry_id) throw new Error("entry_id is required");
    const changed = !this._config || this._config.entry_id !== config.entry_id;
    this._config = { ...config };
    if (changed) {
      this._cfg = null;
      this._preview = null;
      this._wsError = null;
      this._loaded = false;
      this._built = false;
      this._build();
      this._maybeLoad();
    }
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    try {
      this._hass = hass;
      if (!this._config) return;
      this._t = translator(hass.language);
      this._ent = findEntities(hass, this._config.entry_id);
      if (!this._built || langOf(hass.language) !== this._lang) this._build();
      this._maybeLoad();
      this._update();
    } catch (err) {
      this._showError(err);
    }
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (this._timer === null) this._timer = setInterval(() => this._load(), REFRESH_MS);
    if (this._hass && this._loaded) this._load();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    // Lovelace re-parents cards (column changes on resize/rotate): a disconnect
    // followed by a connect in the same task keeps the open studio and its draft.
    setTimeout(() => {
      if (!this.isConnected && this._studio) {
        this._studio.remove();
        this._studio = null;
      }
    }, 0);
  }

  // -- data ---------------------------------------------------------------
  _maybeLoad() {
    if (this._hass && this._config && !this._loaded && !this._loading) this._load();
  }

  async _load() {
    if (!this._hass || !this._config || this._loading) return;
    this._loading = true;
    const entryId = this._config.entry_id;
    try {
      const [cfg, preview] = await Promise.all([
        this._hass.callWS({ type: "ai_prime/config/get", entry_id: entryId }),
        this._hass.callWS({ type: "ai_prime/preview", entry_id: entryId }),
      ]);
      if (!this._config || this._config.entry_id !== entryId) return;
      this._cfg = cfg;
      this._preview = preview;
      this._wsError = null;
    } catch (err) {
      this._wsError = err;
    } finally {
      this._loading = false;
      this._loaded = true;
      this._update();
    }
  }

  async _call(promise) {
    try {
      await promise;
      this._wsError = null;
    } catch (err) {
      this._wsError = err;
    }
    this._update();
  }

  // -- DOM ----------------------------------------------------------------
  _tr(key) {
    return (this._t || translator(this._hass && this._hass.language))(key);
  }

  _fmtNum(stateObj, digits) {
    const unit = stateObj.attributes.unit_of_measurement || "";
    return `${fmtState(stateObj.state, digits)}${unit ? " " + unit : ""}`;
  }

  _showError(err) {
    const msg = err && (err.message || err.code) ? err.message || err.code : String(err);
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="error">${esc(this._tr("error_prefix"))}: ${esc(msg)}</div></ha-card>`;
    this._built = false;
  }

  _build() {
    const root = this.shadowRoot;
    if (!this._config) {
      root.innerHTML = `<style>${STYLE}</style><ha-card><div class="error">${esc(this._tr("error_config"))}</div></ha-card>`;
      return;
    }
    this._lang = langOf(this._hass && this._hass.language);
    this._selCfg = null;
    this._drawnPreview = null;
    this._chipsHtml = null;
    const t = (k) => esc(this._tr(k));
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="wrap">
          <div class="head"><div class="title" id="title"></div><div class="chips" id="chips"></div></div>
          <div class="note" id="note" hidden></div>
          <div class="row"><label>${t("brightness")}</label>
            <input type="range" id="bri" min="0" max="100" step="1"><span class="val" id="briv"></span></div>
          <div class="row"><label>${t("nature_mode")}</label><span id="natureslot"></span></div>
          <div class="row"><label>${t("preset")}</label><select id="preset"></select></div>
          <svg id="curve" viewBox="0 0 ${SVG_W} ${SVG_H}" preserveAspectRatio="none" role="img"></svg>
          <div class="times" id="times"></div>
          <details><summary>${t("channels")}</summary><div id="chans"></div></details>
          <button class="primary" id="setup">${t("setup")}</button>
        </div>
      </ha-card>`;
    const $ = (id) => root.getElementById(id);
    this._el = { title: $("title"), chips: $("chips"), note: $("note"), bri: $("bri"), briv: $("briv"),
      preset: $("preset"), curve: $("curve"), times: $("times"), chans: $("chans") };

    // Nature toggle: ha-switch when the frontend provides it, else a checkbox.
    const toggle = customElements.get("ha-switch") ? document.createElement("ha-switch") : Object.assign(document.createElement("input"), { type: "checkbox" });
    toggle.id = "nature";
    toggle.addEventListener("change", () => {
      const id = this._ent.nature_mode;
      if (!id) return;
      this._call(this._hass.callService("switch", toggle.checked ? "turn_on" : "turn_off", { entity_id: id }));
    });
    $("natureslot").appendChild(toggle);
    this._el.nature = toggle;

    this._el.bri.addEventListener("input", () => { this._el.briv.textContent = `${this._el.bri.value} %`; this._busy = true; });
    this._el.bri.addEventListener("change", () => {
      this._busy = false;
      const id = this._ent.light;
      if (!id) return;
      const pct = Number(this._el.bri.value);
      this._call(pct > 0
        ? this._hass.callService("light", "turn_on", { entity_id: id, brightness_pct: pct })
        : this._hass.callService("light", "turn_off", { entity_id: id }));
    });

    this._el.preset.addEventListener("change", () => {
      this._call(this._hass.callWS({ type: "ai_prime/preset/activate", entry_id: this._config.entry_id, preset_id: this._el.preset.value })
        .finally(() => this._load())); // lamp_rejected still changed the active preset
    });

    this._el.sliders = {};
    for (const c of CHANNEL_ORDER) {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<label><span class="dot" style="display:inline-block;background:${CHANNEL_COLORS[c]}"></span> ${t(c)}</label>
        <input type="range" min="0" max="100" step="0.1"><span class="val"></span>`;
      const input = row.querySelector("input");
      const val = row.querySelector(".val");
      input.addEventListener("input", () => { val.textContent = `${input.value} %`; this._busy = true; });
      input.addEventListener("change", () => {
        this._busy = false;
        const id = this._ent[c];
        if (id) this._call(this._hass.callService("number", "set_value", { entity_id: id, value: Number(input.value) }));
      });
      this._el.chans.appendChild(row);
      this._el.sliders[c] = { input, val };
    }

    $("setup").addEventListener("click", () => this._openStudio());
    this._built = true;
  }

  _openStudio() {
    if (this._studio) return;
    const studio = document.createElement("ai-prime-studio");
    studio.hass = this._hass;
    studio.entryId = this._config.entry_id;
    studio.addEventListener("studio-closed", () => {
      this._studio = null;
      this._load();
    });
    // Mounted at the top of <home-assistant>'s shadow root (fallback <body>):
    // outside the card, whose shadow root is rewritten on rebuilds and whose
    // ancestors may clip a fixed overlay, but still inside HA's element tree,
    // where HA components (e.g. the map, which fetches its tile token through
    // context provided there) keep working.
    this._studio = studio;
    const ha = document.querySelector("home-assistant");
    (ha && ha.shadowRoot ? ha.shadowRoot : document.body).appendChild(studio);
  }

  _state(key) {
    const id = this._ent[key];
    const s = id && this._hass && this._hass.states[id];
    return s && s.state !== "unavailable" && s.state !== "unknown" ? s : null;
  }

  _update() {
    if (!this._built || !this._hass) return;
    const el = this._el;
    if (this._studio) this._studio.hass = this._hass;
    const lightState = this._hass.states[this._ent.light];
    el.title.textContent = (lightState && lightState.attributes.friendly_name) || this._config.entry_id;

    // Chips
    const chips = [];
    const temp = this._state("temperature");
    if (temp) chips.push(`${esc(this._tr("temperature"))} ${esc(this._fmtNum(temp, 1))}`);
    const fan = this._state("fan");
    if (fan) chips.push(esc(this._tr(fan.state === "on" ? "fan_on" : "fan_off")));
    const power = this._state("power");
    if (power) chips.push(esc(this._fmtNum(power, 0)));
    const cloud = this._state("cloud_cover");
    if (cloud) chips.push(`${esc(this._tr("cloud_cover"))} ${esc(this._fmtNum(cloud, 0))}`);
    let html = chips.map((c) => `<span class="chip">${c}</span>`).join("");
    if (this._cfg && this._cfg.lamp_write_pending) html += `<span class="chip warn">${esc(this._tr("pending"))}</span>`;
    if (html !== this._chipsHtml) {
      this._chipsHtml = html;
      el.chips.innerHTML = html;
    }

    // Errors
    el.note.hidden = !this._wsError;
    if (this._wsError) {
      const e = this._wsError;
      el.note.textContent = `${this._tr("error_prefix")}: ${errorText(e, (k) => this._tr(k))}`;
    }

    // Brightness / channels (not while the user drags)
    if (!this._busy) {
      const bri = lightState && lightState.state === "on" ? Math.round(((lightState.attributes.brightness || 0) / 255) * 100) : 0;
      el.bri.value = bri;
      el.briv.textContent = lightState && lightState.state !== "unavailable" ? `${bri} %` : this._tr("unavailable");
      for (const c of CHANNEL_ORDER) {
        const s = this._state(c);
        const slot = el.sliders[c];
        slot.input.disabled = !s;
        slot.input.value = s ? Number(s.state) : 0;
        slot.val.textContent = s ? `${Number(s.state)} %` : "–";
      }
    }
    el.bri.disabled = !lightState || lightState.state === "unavailable";

    // Nature switch
    const nm = this._hass.states[this._ent.nature_mode];
    el.nature.checked = !!nm && nm.state === "on";
    el.nature.disabled = !nm || nm.state === "unavailable";

    // Preset dropdown: rebuilt only when the config changed and the user is not using it
    const focused = this.shadowRoot.activeElement === el.preset;
    if (this._cfg !== this._selCfg && !focused) {
      this._selCfg = this._cfg;
      if (this._cfg) {
        const active = this._cfg.effective_active || this._cfg.active;
        const known = this._cfg.presets.some((p) => p.id === active);
        const leaderName = this._cfg.follow && this._cfg.effective_active_name;
        el.preset.innerHTML =
          (leaderName ? `<option value="" selected disabled>${esc(leaderName)}</option>` : known ? "" : `<option value="" selected disabled>–</option>`) +
          this._cfg.presets
            .map((p) => `<option value="${esc(p.id)}"${p.id === active && !leaderName ? " selected" : ""}>${esc(p.name)}</option>`)
            .join("");
        el.preset.disabled = !!this._cfg.follow; // a follower uses its leader's preset
      } else {
        el.preset.innerHTML = `<option>${esc(this._tr(this._loaded ? "unavailable" : "loading"))}</option>`;
        el.preset.disabled = true;
      }
    }

    this._drawCurve();
  }

  _drawCurve() {
    const el = this._el;
    const p = this._preview;
    if (p === this._drawnPreview && (p || !this._loaded)) return;
    this._drawnPreview = p;
    const curve = p && p.curve;
    if (!curve || !curve.t || !curve.t.length) {
      el.curve.innerHTML = "";
      el.times.textContent = this._loaded ? this._tr("no_curve") : "";
      return;
    }
    const x = (t) => ((t / 1440) * SVG_W).toFixed(1);
    const y = (v) => (SVG_H - (Math.min(100, Math.max(0, v)) / 100) * (SVG_H - 4) - 2).toFixed(1);
    let svg = [25, 50, 75].map((g) => `<line class="grid" x1="0" x2="${SVG_W}" y1="${y(g)}" y2="${y(g)}"/>`).join("");
    for (const m of p.markers || []) svg += `<line class="mk" x1="${x(m.t)}" x2="${x(m.t)}" y1="0" y2="${SVG_H}"/>`;
    for (const c of CHANNEL_ORDER) {
      const series = curve.effective[c] || [];
      const pts = series.map((v, i) => `${x(curve.t[i])},${y(v)}`).join(" ");
      svg += `<polyline fill="none" stroke="${CHANNEL_COLORS[c]}" stroke-width="1.5" vector-effect="non-scaling-stroke" points="${pts}"/>`;
    }
    const nowMin = nowMinutes(this._hass.config && this._hass.config.time_zone);
    svg += `<line class="now" x1="${x(nowMin)}" x2="${x(nowMin)}" y1="0" y2="${SVG_H}"><title>${esc(this._tr("now"))}</title></line>`;
    el.curve.innerHTML = svg;

    const parts = [];
    if (p.sun && p.sun.home) {
      parts.push(`${this._tr("sunrise")} ${fmtTime(p.sun.home[0])}`, `${this._tr("sunset")} ${fmtTime(p.sun.home[1])}`);
    }
    if (p.moon) {
      const iv = (p.moon.intervals || [])[0];
      parts.push(`${this._tr("moon")} ${p.moon.phase} %${iv ? ` (${fmtTime(iv[0])}–${fmtTime(iv[1])})` : ""}`);
    }
    el.times.innerHTML = parts.map((s) => `<span>${esc(s)}</span>`).join("");
  }
}

class AiPrimeCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._entries = null;
    this._error = null;
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._loadEntries();
  }

  get hass() {
    return this._hass;
  }

  async _loadEntries() {
    try {
      this._entries = await this._hass.callWS({ type: "ai_prime/entries" });
      this._error = null;
    } catch (err) {
      this._error = err;
    }
    this._render();
  }

  _render() {
    const t = translator(this._hass && this._hass.language);
    const style = `<style>
      .ed { padding: 8px 0; display: flex; flex-direction: column; gap: 8px; }
      label { color: var(--secondary-text-color); font-size: 14px; }
      select { padding: 10px; font: inherit; border-radius: 8px; color: var(--primary-text-color);
        background: var(--card-background-color); border: 1px solid var(--divider-color); }
      .error { color: var(--error-color); }
    </style>`;
    if (this._error) {
      this.shadowRoot.innerHTML = `${style}<div class="ed error">${esc(t("error_prefix"))}: ${esc(this._error.message || this._error.code || this._error)}</div>`;
      return;
    }
    if (!this._entries) {
      this.shadowRoot.innerHTML = `${style}<div class="ed">${esc(t("loading"))}</div>`;
      return;
    }
    if (!this._entries.length) {
      this.shadowRoot.innerHTML = `${style}<div class="ed">${esc(t("no_lamps"))}</div>`;
      return;
    }
    const current = this._config.entry_id || "";
    const options = this._entries
      .map((e) => `<option value="${esc(e.entry_id)}"${e.entry_id === current ? " selected" : ""}>${esc(e.title)}</option>`)
      .join("");
    this.shadowRoot.innerHTML = `${style}<div class="ed"><label for="lamp">${esc(t("select_lamp"))}</label>
      <select id="lamp"><option value=""${current ? "" : " selected"} disabled>${esc(t("select_lamp_placeholder"))}</option>${options}</select></div>`;
    this.shadowRoot.getElementById("lamp").addEventListener("change", (ev) => {
      this._config = { ...this._config, entry_id: ev.target.value };
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
    });
  }
}

if (!customElements.get("ai-prime-card")) customElements.define("ai-prime-card", AiPrimeCard);
if (!customElements.get("ai-prime-card-editor")) customElements.define("ai-prime-card-editor", AiPrimeCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "ai-prime-card")) {
  window.customCards.push({
    type: "ai-prime-card",
    name: "AI Prime Studio",
    description: translator(document.documentElement.lang)("card_description"),
  });
}
