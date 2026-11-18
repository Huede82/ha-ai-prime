// Studio tab "Orte": weather / sun / moon locations, time modes, follow leader.

import { errorText, translator } from "./i18n.js";
import { fmtTime, formatPlace, samePlace } from "./model.js";

const BLOCKS = ["weather", "sun", "moon"]; // option keys are `<block>_location`
const MODE_BLOCKS = ["sun", "moon"];
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const STYLE = `
  :host { display: block; max-width: 960px; margin: 0 auto; color: var(--primary-text-color, #212121); }
  .blocks { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  section.block { padding: 16px; border-radius: 12px; display: flex; flex-direction: column; gap: 10px;
    background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #ddd); }
  h2 { margin: 0; font-size: 18px; font-weight: 500; }
  .place { font-size: 14px; color: var(--secondary-text-color, #727272); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .row > label { font-size: 13px; color: var(--secondary-text-color, #727272); }
  input[type=text], input[type=number], select { padding: 8px; font: inherit; border-radius: 8px; min-width: 0;
    color: var(--primary-text-color, #212121); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ccc); }
  input[type=number] { width: 8em; }
  input[type=text] { flex: 1 1 140px; }
  button { cursor: pointer; border: 0; border-radius: 8px; padding: 8px 16px; font: inherit;
    background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  button:disabled { opacity: .5; cursor: default; }
  button.secondary { background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
  ul.results { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  ul.results button { width: 100%; text-align: left; }
  .info { font-size: 13px; padding-top: 8px; border-top: 1px solid var(--divider-color, #ddd);
    display: flex; flex-direction: column; gap: 2px; color: var(--secondary-text-color, #727272); }
  .follow { margin-top: 16px; display: flex; flex-direction: column; gap: 8px; }
  .hint { font-size: 13px; color: var(--secondary-text-color, #727272); }
  .actions { margin-top: 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .error { color: var(--error-color, #db4437); font-size: 14px; }
  .ok { color: var(--success-color, #43a047); font-size: 14px; }
  [hidden] { display: none !important; }
  ha-selector { display: block; }
`;

export class AiPrimeTabLocations extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._entryId = null;
    this._cfg = null;
    this._draft = null;
    this._base = null; // JSON of the draft as loaded; dirty = differs
    this._preview = null;
    this._search = {}; // block -> {query, results, busy, error}
    this._error = null;
    this._status = null;
    this._saving = false;
    this._selectors = [];
    this._pendingFollow = null;
  }

  set hass(hass) {
    this._hass = hass;
    for (const sel of this._selectors) sel.hass = hass;
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
    const followChanged = !!this._cfg && this._cfg.follow !== (cfg && cfg.follow);
    this._cfg = cfg;
    if (cfg && (!this._draft || !this.isDirty() || followChanged)) this._resetDraft();
    this._render();
  }

  get cfg() {
    return this._cfg;
  }

  connectedCallback() {
    this._render();
    this._loadPreview();
  }

  // -- dirty guard ----------------------------------------------------------
  isDirty() {
    return !!this._draft && JSON.stringify(this._draft) !== this._base;
  }

  /** False while there are unsaved edits (studio shows its discard prompt). */
  canLeave() {
    return !this.isDirty();
  }

  discard() {
    this._resetDraft();
  }

  _following() {
    return !!(this._cfg && this._cfg.follow);
  }

  _resetDraft() {
    const cfg = this._cfg;
    // A follower shows (read-only) the values of its leader.
    const source = (this._following() ? cfg.effective_locations : cfg.locations) || {};
    const draft = {};
    for (const b of BLOCKS) draft[b] = { ...(source[`${b}_location`] || cfg.home) };
    for (const b of MODE_BLOCKS) draft[`${b}_mode`] = source[`${b}_mode`] || "anchored";
    this._draft = draft;
    this._base = JSON.stringify(draft);
    this._error = null;
  }

  async _loadPreview() {
    if (!this._hass || !this._entryId) return;
    try {
      this._preview = await this._hass.callWS({ type: "ai_prime/preview", entry_id: this._entryId });
    } catch (_err) {
      this._preview = null;
    }
    this._render();
  }

  // -- actions --------------------------------------------------------------
  _setLocation(block, loc) {
    this._draft[block] = loc;
    this._status = null;
    this._render();
  }

  async _doSearch(block) {
    const state = this._search[block] || (this._search[block] = {});
    const query = (state.query || "").trim();
    if (!query) return;
    state.busy = true;
    state.error = null;
    this._render();
    try {
      state.results = await this._hass.callWS({ type: "ai_prime/geocode", query });
    } catch (err) {
      state.results = null;
      state.error = errorText(err, this._t());
    }
    state.busy = false;
    this._render();
  }

  _payload() {
    const home = this._cfg.home;
    const locations = {};
    for (const b of BLOCKS) {
      const loc = this._draft[b];
      locations[`${b}_location`] = samePlace(loc, home) && !loc.name
        ? null
        : { latitude: loc.latitude, longitude: loc.longitude, ...(loc.name ? { name: loc.name } : {}) };
    }
    for (const b of MODE_BLOCKS) locations[`${b}_mode`] = this._draft[`${b}_mode`];
    return locations;
  }

  async _save() {
    this._error = null;
    this._status = null;
    this._saving = true;
    const snapshot = JSON.stringify(this._draft); // edits made while saving stay dirty
    const locations = this._payload();
    this._render();
    try {
      await this._hass.callWS({ type: "ai_prime/locations/set", entry_id: this._entryId, locations });
      this._base = snapshot;
      this._status = "saved";
      this.dispatchEvent(new CustomEvent("studio-reload", { bubbles: true, composed: true }));
      this._loadPreview();
    } catch (err) {
      this._error = errorText(err, this._t());
    }
    this._saving = false;
    this._render();
  }

  _requestFollow(leader) {
    if (this.isDirty()) {
      this._pendingFollow = { leader };
      this._render();
      return;
    }
    this._setFollow(leader);
  }

  async _setFollow(leader) {
    this._error = null;
    this._status = null;
    try {
      await this._hass.callWS({ type: "ai_prime/follow/set", entry_id: this._entryId, leader: leader || null });
      this.dispatchEvent(new CustomEvent("studio-reload", { bubbles: true, composed: true }));
      this._loadPreview();
    } catch (err) {
      this._error = errorText(err, this._t());
      // lamp_rejected: the follow change is stored, only the lamp refused the schedule.
      if (err && err.code === "lamp_rejected") {
        this.dispatchEvent(new CustomEvent("studio-reload", { bubbles: true, composed: true }));
        this._loadPreview();
      }
      this._render();
    }
  }

  // -- rendering ------------------------------------------------------------
  _t() {
    return translator(this._hass && this._hass.language);
  }

  _info(block, t) {
    const p = this._preview;
    if (!p) return `<div>${esc(t("info_none"))}</div>`;
    const lines = [];
    if (block === "weather") {
      if (!p.weather) return `<div>${esc(t("info_none"))}</div>`;
      lines.push(`${t("info_clouds")}: ${p.weather.cloud_cover} %`);
      lines.push(`${t("info_thunder")}: ${t(p.weather.thunderstorm ? "yes" : "no")}`);
    } else if (block === "sun") {
      if (!p.sun || (!p.sun.home && !p.sun.local)) return `<div>${esc(t("info_none"))}</div>`;
      if (p.sun.local) lines.push(`${t("info_local")}: ${p.sun.local.rise} / ${p.sun.local.set} (${p.sun.local.tz})`);
      if (p.sun.home) lines.push(`${t("info_home")}: ${fmtTime(p.sun.home[0])} / ${fmtTime(p.sun.home[1])}`);
      if (p.sun.day_length_min != null) lines.push(`${t("info_day_length")}: ${fmtTime(p.sun.day_length_min)}`);
    } else if (p.moon) {
      const iv = (p.moon.intervals || []).map(([a, b]) => `${fmtTime(a)}–${fmtTime(b)}`).join(", ");
      lines.push(`${t("info_up")}: ${iv || "–"}`);
      lines.push(`${t("info_phase")}: ${p.moon.phase} %`);
    }
    return lines.map((l) => `<div>${esc(l)}</div>`).join("");
  }

  _render() {
    const t = this._t();
    const root = this.shadowRoot;
    this._selectors = [];
    if (!this._cfg || !this._draft) {
      root.innerHTML = `<style>${STYLE}</style><div class="hint">${esc(t("loading"))}</div>`;
      return;
    }
    const ro = this._following();
    const dirty = this.isDirty();
    const blocks = BLOCKS.map((b) => {
      const loc = this._draft[b];
      const home = samePlace(loc, this._cfg.home) && !loc.name;
      const s = this._search[b] || {};
      const results = s.results
        ? s.results.length
          ? `<ul class="results">${s.results
              .map((r, i) => `<li><button class="secondary" data-pick="${b}:${i}">${esc([r.name, r.admin1, r.country].filter(Boolean).join(", "))}</button></li>`)
              .join("")}</ul>`
          : `<div class="hint">${esc(t("no_results"))}</div>`
        : "";
      const modes = MODE_BLOCKS.includes(b)
        ? `<div class="row" role="radiogroup" aria-label="${esc(t("time_mode"))}"><label>${esc(t("time_mode"))}</label>
            ${["anchored", "real"]
              .map((m) => `<label><input type="radio" name="${b}_mode" value="${m}"${this._draft[`${b}_mode`] === m ? " checked" : ""}${ro ? " disabled" : ""}> ${esc(t(`mode_${m}`))}</label>`)
              .join("")}</div>`
        : "";
      return `<section class="block" data-block="${b}">
        <h2>${esc(t(`block_${b}`))}</h2>
        <div class="place">${esc(home ? t("home") : formatPlace(loc))}${home ? "" : ` (${Number(loc.latitude).toFixed(4)}, ${Number(loc.longitude).toFixed(4)})`}</div>
        <div class="picker" data-picker="${b}"></div>
        ${ro ? "" : `<div class="row">
          <input type="text" data-q="${b}" maxlength="100" placeholder="${esc(t("place_search"))}" value="${esc(s.query || "")}" aria-label="${esc(t("place_search"))}">
          <button data-search="${b}"${s.busy ? " disabled" : ""}>${esc(t("search"))}</button>
          <button class="secondary" data-home="${b}">${esc(t("home"))}</button></div>`}
        ${s.error ? `<div class="error">${esc(t("error_prefix"))}: ${esc(s.error)}</div>` : ""}
        ${results}
        ${modes}
        <div class="info" data-info="${b}">${this._info(b, t)}</div>
      </section>`;
    }).join("");

    const followable = this._cfg.followable || [];
    const leader = this._cfg.follow;
    const options = [`<option value=""${leader ? "" : " selected"}>${esc(t("follows_none"))}</option>`]
      .concat(followable.map((f) => `<option value="${esc(f.entry_id)}"${f.entry_id === leader ? " selected" : ""}>${esc(f.title)}</option>`));
    if (leader && !followable.some((f) => f.entry_id === leader)) {
      options.push(`<option value="${esc(leader)}" selected>${esc(leader)}</option>`);
    }

    root.innerHTML = `<style>${STYLE}</style>
      <div class="blocks">${blocks}</div>
      <div class="follow"><div class="row"><label for="follow">${esc(t("follows"))}</label>
        <select id="follow">${options.join("")}</select></div>
        ${ro ? `<div class="hint">${esc(t("follows_hint"))}</div>` : ""}</div>
      ${this._pendingFollow ? `<div class="error" id="fprompt" role="alert">${esc(t("unsaved_prompt"))}
        <button id="fdiscard">${esc(t("discard"))}</button> <button id="fstay" class="secondary">${esc(t("stay"))}</button></div>` : ""}
      <div class="actions">
        <button id="save"${!dirty || ro || this._saving ? " disabled" : ""}>${esc(t("save"))}</button>
        <span class="error" id="err"${this._error ? "" : " hidden"}>${esc(t("error_prefix"))}: ${esc(this._error || "")}</span>
        <span class="ok" id="ok"${this._status === "saved" ? "" : " hidden"}>${esc(t("saved"))}</span>
      </div>`;

    for (const b of BLOCKS) this._mountPicker(b, ro);
    this._wire(ro);
  }

  /** Map picker (ha-selector) when the frontend provides it, else lat/lon inputs. */
  _mountPicker(block, readonly) {
    const slot = this.shadowRoot.querySelector(`[data-picker="${block}"]`);
    const loc = this._draft[block];
    if (readonly) return;
    if (customElements.get("ha-selector")) {
      const sel = document.createElement("ha-selector");
      sel.hass = this._hass;
      sel.selector = { location: { radius: false } };
      sel.value = { latitude: loc.latitude, longitude: loc.longitude };
      sel.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const v = ev.detail && ev.detail.value;
        if (!v || typeof v.latitude !== "number" || typeof v.longitude !== "number") return;
        if (samePlace(v, this._draft[block])) return;
        this._draft[block] = { latitude: v.latitude, longitude: v.longitude };
        this._status = null;
        this._updateBlock(block);
      });
      slot.appendChild(sel);
      this._selectors.push(sel);
      return;
    }
    slot.innerHTML = `<div class="row">
      <label>${esc(this._t()("latitude"))} <input type="number" step="any" min="-90" max="90" data-lat="${block}" value="${loc.latitude}"></label>
      <label>${esc(this._t()("longitude"))} <input type="number" step="any" min="-180" max="180" data-lon="${block}" value="${loc.longitude}"></label></div>`;
    const onChange = () => {
      const lat = Number(slot.querySelector("[data-lat]").value);
      const lon = Number(slot.querySelector("[data-lon]").value);
      const ok = slot.querySelector("[data-lat]").value !== "" && slot.querySelector("[data-lon]").value !== ""
        && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
      if (!ok) {
        this._error = this._t()("invalid_coords");
        this._refreshActions();
        return;
      }
      this._error = null;
      this._status = null;
      this._draft[block] = { latitude: lat, longitude: lon };
      this._updateBlock(block);
    };
    for (const input of slot.querySelectorAll("input")) input.addEventListener("change", onChange);
  }

  /** Cheap update after a map/coordinate edit: keep the pickers, refresh labels and save state. */
  _updateBlock(block) {
    const t = this._t();
    const loc = this._draft[block];
    const home = samePlace(loc, this._cfg.home) && !loc.name;
    const place = this.shadowRoot.querySelector(`[data-block="${block}"] .place`);
    if (place) place.textContent = `${home ? t("home") : formatPlace(loc)}${home ? "" : ` (${Number(loc.latitude).toFixed(4)}, ${Number(loc.longitude).toFixed(4)})`}`;
    this._refreshActions();
  }

  _refreshActions() {
    const root = this.shadowRoot;
    const save = root.getElementById("save");
    if (save) save.disabled = !this.isDirty() || this._following() || this._saving;
    const err = root.getElementById("err");
    if (err) {
      err.hidden = !this._error;
      err.textContent = this._error ? `${this._t()("error_prefix")}: ${this._error}` : "";
    }
    const ok = root.getElementById("ok");
    if (ok) ok.hidden = this._status !== "saved";
  }

  _wire(readonly) {
    const root = this.shadowRoot;
    root.getElementById("follow").addEventListener("change", (ev) => this._requestFollow(ev.target.value));
    const fdiscard = root.getElementById("fdiscard");
    if (fdiscard) {
      fdiscard.addEventListener("click", () => {
        const { leader } = this._pendingFollow;
        this._pendingFollow = null;
        this._resetDraft();
        this._setFollow(leader);
      });
      root.getElementById("fstay").addEventListener("click", () => {
        this._pendingFollow = null;
        this._render();
      });
    }
    if (readonly) return;
    root.getElementById("save").addEventListener("click", () => this._save());
    for (const input of root.querySelectorAll("[data-q]")) {
      const b = input.dataset.q;
      input.addEventListener("input", () => {
        (this._search[b] || (this._search[b] = {})).query = input.value;
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") this._doSearch(b);
      });
    }
    for (const btn of root.querySelectorAll("[data-search]")) btn.addEventListener("click", () => this._doSearch(btn.dataset.search));
    for (const btn of root.querySelectorAll("[data-home]")) {
      btn.addEventListener("click", () => this._setLocation(btn.dataset.home, { ...this._cfg.home }));
    }
    for (const btn of root.querySelectorAll("[data-pick]")) {
      btn.addEventListener("click", () => {
        const [b, i] = btn.dataset.pick.split(":");
        const r = this._search[b].results[Number(i)];
        this._search[b] = { query: this._search[b].query };
        this._setLocation(b, { latitude: r.latitude, longitude: r.longitude, name: r.name });
      });
    }
    for (const radio of root.querySelectorAll('input[type=radio]')) {
      radio.addEventListener("change", () => {
        this._draft[radio.name] = radio.value;
        this._status = null;
        this._refreshActions();
      });
    }
  }
}

if (!customElements.get("ai-prime-tab-locations")) customElements.define("ai-prime-tab-locations", AiPrimeTabLocations);
