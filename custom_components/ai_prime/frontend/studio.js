// Full-screen studio overlay: tab bar plus one pane per tab.
//
// Tab panes are custom elements with props `hass`, `entryId`, `cfg` (result of
// ai_prime/config/get) and a method `canLeave(): boolean` (false = unsaved
// changes). A pane asks for fresh config by dispatching `studio-reload`.

import "./tab-locations.js";
import "./tab-presets.js";
import "./tab-preview.js";
import { translator } from "./i18n.js";

const TABS = [
  { id: "locations", label: "tab_locations", tag: "ai-prime-tab-locations" },
  { id: "schedules", label: "tab_schedules", tag: "ai-prime-tab-presets" },
  { id: "preview", label: "tab_preview", tag: "ai-prime-tab-preview" },
];
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export class AiPrimeStudio extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._entryId = null;
    this._cfg = null;
    this._tab = "locations";
    this._pane = null;
    this._pending = null; // action waiting for the discard prompt
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._pane) this._pane.hass = hass;
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

  connectedCallback() {
    this._render();
    this._reload();
  }

  async _reload() {
    if (!this._hass || !this._entryId) return;
    try {
      this._cfg = await this._hass.callWS({ type: "ai_prime/config/get", entry_id: this._entryId });
      this._cfgError = null;
    } catch (err) {
      this._cfgError = err;
    }
    this._showError();
    if (this._pane) this._pane.cfg = this._cfg;
  }

  _showError() {
    const box = this.shadowRoot.getElementById("err");
    if (!box) return;
    const t = translator(this._hass && this._hass.language);
    box.hidden = !this._cfgError;
    if (this._cfgError) box.textContent = `${t("error_prefix")}: ${this._cfgError.message || this._cfgError.code || this._cfgError}`;
  }

  /** Run `action` now, or after the user confirmed discarding unsaved changes. */
  _guarded(action) {
    if (!this._pane || this._pane.canLeave()) {
      action();
      return;
    }
    this._pending = action;
    this._renderPrompt();
  }

  close() {
    this.dispatchEvent(new CustomEvent("studio-closed", { bubbles: true, composed: true }));
    this.remove();
  }

  _selectTab(id) {
    if (id === this._tab) return;
    this._guarded(() => {
      this._tab = id;
      this._mountPane();
      this._renderTabs();
    });
  }

  _renderPrompt() {
    const t = translator(this._hass && this._hass.language);
    const box = this.shadowRoot.getElementById("prompt");
    box.hidden = !this._pending;
    if (!this._pending) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = `<span>${esc(t("unsaved_prompt"))}</span>
      <button id="discard">${esc(t("discard"))}</button><button id="stay" class="secondary">${esc(t("stay"))}</button>`;
    box.querySelector("#discard").addEventListener("click", () => {
      const action = this._pending;
      this._pending = null;
      this._renderPrompt();
      if (this._pane && this._pane.discard) this._pane.discard();
      action();
    });
    box.querySelector("#stay").addEventListener("click", () => {
      this._pending = null;
      this._renderPrompt();
    });
  }

  _renderTabs() {
    const t = translator(this._hass && this._hass.language);
    const bar = this.shadowRoot.getElementById("tabs");
    bar.innerHTML = TABS.map(
      (tab) => `<button role="tab" data-tab="${tab.id}" aria-selected="${tab.id === this._tab}"${tab.id === this._tab ? ' class="on"' : ""}>${esc(t(tab.label))}</button>`,
    ).join("");
    for (const b of bar.querySelectorAll("button")) b.addEventListener("click", () => this._selectTab(b.dataset.tab));
  }

  _mountPane() {
    const main = this.shadowRoot.getElementById("pane");
    main.textContent = "";
    this._pane = null;
    const def = TABS.find((x) => x.id === this._tab);
    const pane = document.createElement(def.tag);
    pane.hass = this._hass;
    pane.entryId = this._entryId;
    pane.cfg = this._cfg;
    pane.addEventListener("studio-reload", () => this._reload());
    main.appendChild(pane);
    this._pane = pane;
  }

  _render() {
    const t = translator(this._hass && this._hass.language);
    this.shadowRoot.innerHTML = `
      <style>
        .overlay { position: fixed; inset: 0; z-index: 1000; display: flex; flex-direction: column;
          background: var(--primary-background-color, #fafafa); color: var(--primary-text-color, #212121);
          font-family: var(--paper-font-body1_-_font-family, inherit); }
        header { display: flex; align-items: center; justify-content: space-between; gap: 12px;
          padding: 12px 16px; background: var(--app-header-background-color, var(--card-background-color, #fff));
          border-bottom: 1px solid var(--divider-color, #ddd); }
        h1 { margin: 0; font-size: 20px; font-weight: 400; }
        button { cursor: pointer; border: 0; border-radius: 8px; padding: 8px 16px; font: inherit;
          background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
        button.secondary { background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
        #tabs { display: flex; gap: 4px; padding: 0 8px; overflow-x: auto;
          border-bottom: 1px solid var(--divider-color, #ddd); }
        #tabs button { background: none; color: var(--secondary-text-color, #727272); border-radius: 0;
          border-bottom: 3px solid transparent; white-space: nowrap; }
        #tabs button.on { color: var(--primary-color, #03a9f4); border-bottom-color: var(--primary-color, #03a9f4); }
        #prompt { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 16px;
          background: var(--warning-color, #ffa600); color: var(--text-primary-color, #fff); }
        #err { padding: 8px 16px; color: var(--error-color, #db4437); }
        main { flex: 1; overflow: auto; padding: 16px; }
        [hidden] { display: none !important; }
      </style>
      <div class="overlay" role="dialog" aria-modal="true">
        <header><h1>${esc(t("studio_title"))}</h1><button id="close">${esc(t("close"))}</button></header>
        <div id="tabs" role="tablist"></div>
        <div id="prompt" hidden></div>
        <div id="err" hidden></div>
        <main id="pane"></main>
      </div>`;
    this.shadowRoot.getElementById("close").addEventListener("click", () => this._guarded(() => this.close()));
    this._built = true;
    this._renderTabs();
    this._renderPrompt();
    this._showError();
    this._mountPane();
  }
}

if (!customElements.get("ai-prime-studio")) customElements.define("ai-prime-studio", AiPrimeStudio);
