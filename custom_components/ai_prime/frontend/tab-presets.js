// Studio tab "Farbschema": colour-scheme library (own presets + templates) plus curve / table editor.
// Edits never overwrite a stored preset: every save creates a new library entry.

import "./curve-editor.js";
import "./table-editor.js";
import { errorText, translator } from "./i18n.js";
import { CHANNEL_ORDER, MAX_NAME, validate } from "./model.js";

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const clonePoints = (pts) => pts.map((p) => ({ t: p.t, v: { ...p.v } }));
/** Unique "<base> <n>" name: strips a trailing " <n>", skips names already in the library, max MAX_NAME chars. */
export function suggestName(name, existing) {
  const taken = new Set(existing.map((n) => String(n).toLowerCase()));
  const base = String(name).replace(/ \d+$/, "");
  for (let n = 2; ; n++) {
    const suffix = ` ${n}`;
    const cand = base.slice(0, MAX_NAME - suffix.length).trimEnd() + suffix;
    if (!taken.has(cand.toLowerCase())) return cand;
  }
}

// Wire/storage form: only the five channels, nothing else.
const wirePoints = (pts) => pts.map((p) => ({ t: p.t, v: Object.fromEntries(CHANNEL_ORDER.map((c) => [c, p.v[c]])) }));

const STYLE = `
  :host { display: block; max-width: 1200px; margin: 0 auto; color: var(--primary-text-color, #212121); }
  .layout { display: grid; grid-template-columns: 1fr; gap: 16px; }
  @media (min-width: 900px) { .layout { grid-template-columns: 300px 1fr; align-items: start; } }
  section { padding: 16px; border-radius: 12px; display: flex; flex-direction: column; gap: 10px;
    background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #ddd); min-width: 0; }
  h2 { margin: 0; font-size: 18px; font-weight: 500; }
  h3 { margin: 6px 0 0; font-size: 14px; font-weight: 500; color: var(--secondary-text-color, #727272); }
  ul.lib { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  ul.lib button.item { width: 100%; text-align: left; display: flex; justify-content: space-between; gap: 8px; min-height: 40px;
    background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
  ul.lib button.item.on { outline: 2px solid var(--primary-color, #03a9f4); }
  .badge { font-size: 12px; padding: 1px 8px; border-radius: 10px; white-space: nowrap;
    background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .badge.tpl { background: var(--secondary-text-color, #727272); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  input[type=text], select { padding: 8px; font: inherit; border-radius: 8px; min-width: 0;
    color: var(--primary-text-color, #212121); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ccc); }
  input[type=text] { flex: 1 1 140px; }
  button { cursor: pointer; border: 0; border-radius: 8px; padding: 8px 16px; font: inherit; min-height: 36px;
    background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  button:disabled { opacity: .5; cursor: default; }
  button.secondary { background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); }
  .prompt { padding: 8px; border-radius: 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
    border: 1px solid var(--warning-color, #ffa600); }
  .hint { font-size: 13px; color: var(--secondary-text-color, #727272); }
  .error { color: var(--error-color, #db4437); font-size: 14px; }
  .ok { color: var(--success-color, #43a047); font-size: 14px; }
  .seg button.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  [hidden] { display: none !important; }
`;

export class AiPrimeTabPresets extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._entryId = null;
    this._cfg = null;
    this._selId = null; // own preset selected in the library
    this._tplId = null; // or a template (read-only source), never both
    this._scrollPrompt = false;
    this._selectName = null; // preset name to select once it shows up in the reloaded config
    this._points = null;
    this._base = null; // JSON of the points as loaded; dirty = differs
    this._view = "curve";
    this._sel = null; // selected point index in the curve editor
    this._prompt = null; // {kind, ...} inline prompt
    this._value = ""; // text field of a name prompt
    this._error = null;
    this._status = null;
    this._busy = false;
    this._editor = null;
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
    const followChanged = !!this._cfg && this._cfg.follow !== (cfg && cfg.follow);
    this._cfg = cfg;
    if (cfg && this._selectName) {
      const made = [...cfg.presets].reverse().find((p) => p.name === this._selectName);
      if (made) {
        const error = this._error;
        this._selId = made.id;
        this._tplId = null;
        this._resetDraft();
        this._error = error;
      }
      this._selectName = null;
    }
    if (cfg) {
      const cur = this._current();
      if (!cur) {
        // Initial load or the edited preset vanished: pick the active one.
        this._selId = (cfg.presets.find((p) => p.id === cfg.active) || cfg.presets[0] || {}).id || null;
        this._tplId = null;
        this._resetDraft();
      } else if (!this.isDirty() || followChanged) {
        const error = this._error; // e.g. lamp_rejected: stored, but keep telling the user
        this._resetDraft();
        this._error = error;
      }
    }
    this._render();
  }

  get cfg() {
    return this._cfg;
  }

  connectedCallback() {
    this._render();
  }

  // -- dirty guard ----------------------------------------------------------
  isDirty() {
    return !!this._points && JSON.stringify(this._points) !== this._base;
  }

  canLeave() {
    return !this.isDirty();
  }

  discard() {
    this._resetDraft();
  }

  _t() {
    return translator(this._hass && this._hass.language);
  }

  _following() {
    return !!(this._cfg && this._cfg.follow);
  }

  /** Selected library entry; a template is returned as a read-only entry flagged `template`. */
  _current() {
    if (!this._cfg) return null;
    if (this._tplId) {
      const x = this._templates().find((tp) => tp.id === this._tplId);
      return x ? { ...x, readonly: true, template: true } : null;
    }
    return this._cfg.presets.find((p) => p.id === this._selId) || null;
  }

  /** Templates offered below the own presets; "original" is skipped when it is already an own preset. */
  _templates() {
    const cfg = this._cfg;
    const hasOriginal = cfg.presets.some((p) => p.id === "original");
    return (cfg.templates || []).filter((x) => !(hasOriginal && x.id === "original"));
  }

  /** Editing is only blocked while following; saving always creates a new entry. */
  _locked() {
    return !this._current() || this._following();
  }

  _resetDraft() {
    const cur = this._current();
    this._points = cur ? clonePoints(cur.points) : null;
    this._base = this._points ? JSON.stringify(this._points) : null;
    this._sel = null;
    this._error = null;
    this._prompt = null;
  }

  // -- actions --------------------------------------------------------------
  /** Run `action` now or, when there are unsaved edits, after the user discards them. */
  _guard(action) {
    if (!this.isDirty()) {
      action();
      return;
    }
    this._showPrompt({ kind: "discard", action });
  }

  /** Show an inline prompt and bring it (and its input) into view; it can render off-screen on phones. */
  _showPrompt(prompt, value = "") {
    this._prompt = prompt;
    this._value = value;
    this._error = null;
    this._scrollPrompt = true;
    this._render();
  }

  _selectEntry(kind, id) {
    if (kind === "tpl" ? id === this._tplId : !this._tplId && id === this._selId) return;
    this._guard(() => {
      this._selId = kind === "tpl" ? null : id;
      this._tplId = kind === "tpl" ? id : null;
      this._status = null;
      this._resetDraft();
      this._render();
    });
  }

  _reload() {
    this.dispatchEvent(new CustomEvent("studio-reload", { bubbles: true, composed: true }));
  }

  async _call(msg) {
    this._error = null;
    this._status = null;
    this._busy = true;
    this._refresh();
    try {
      return { ok: true, result: await this._hass.callWS({ entry_id: this._entryId, ...msg }) };
    } catch (err) {
      this._error = errorText(err, this._t());
      // lamp_rejected: the change is stored in HA, only the lamp refused it.
      return { ok: !!err && err.code === "lamp_rejected", result: null };
    } finally {
      this._busy = false;
    }
  }

  async _confirmPrompt() {
    const p = this._prompt;
    if (!p || this._busy) return;
    const name = this._value.trim();
    if (p.kind === "discard") {
      this._prompt = null;
      this._resetDraft();
      p.action();
      return;
    }
    if (p.kind === "delete") {
      const r = await this._call({ type: "ai_prime/preset/delete", preset_id: this._selId });
      this._prompt = null;
      if (r.ok) {
        this._selId = null; // cfg setter picks the active preset
        this._reload();
        this._status = "deleted";
      }
      this._render();
      return;
    }
    if (!name) return;
    if (p.kind === "rename") {
      const r = await this._call({ type: "ai_prime/preset/rename", preset_id: this._selId, name });
      this._prompt = null;
      if (r.ok) this._reload();
      this._render();
      return;
    }
    // save as new: no preset_id, so the backend creates a library entry; then switch to it.
    if (this._locked() || !validate(this._points).ok) return;
    const r = await this._call({
      type: "ai_prime/preset/save",
      name,
      points: wirePoints(this._points),
      activate: !!p.activate,
    });
    this._prompt = null;
    if (r.ok) {
      this._status = "saved";
      if (r.result) {
        this._selId = r.result.id;
        this._tplId = null;
        this._points = null; // adopt the stored points once the config reloads
      } else {
        // lamp_rejected: stored, but the error reply carries no id; select the new entry by name on reload.
        this._selectName = name;
      }
      this._reload();
    }
    this._render();
  }

  /** Open the name prompt for saving the draft as a new preset (optionally activating it). */
  _save(activate) {
    const cur = this._current();
    if (!cur || this._locked() || this._busy || !validate(this._points).ok) return;
    const own = this._cfg.presets.map((p) => p.name);
    this._showPrompt({ kind: "saveas", activate }, suggestName(cur.name, own));
  }

  async _activate() {
    const r = await this._call({ type: "ai_prime/preset/activate", preset_id: this._selId });
    if (r.ok) {
      this._status = "activated";
      this._reload();
    }
    this._render();
  }

  _onPoints(points) {
    this._points = points;
    this._status = null;
    this._error = null;
    this._refresh();
  }

  // -- rendering ------------------------------------------------------------
  _validationText(res, t) {
    if (res.ok) return "";
    const key = `err_${res.error}`;
    const where = res.index != null ? ` (${t("row")} ${res.index + 1})` : "";
    return `${t(key)}${where}`;
  }

  _render() {
    const t = this._t();
    const root = this.shadowRoot;
    this._editor = null;
    if (!this._cfg) {
      root.innerHTML = `<style>${STYLE}</style><div class="hint">${esc(t("loading"))}</div>`;
      return;
    }
    const cfg = this._cfg;
    const cur = this._current();
    const following = this._following();
    const leader = following ? (cfg.followable || []).find((f) => f.entry_id === cfg.follow) : null;
    const leaderName = leader ? leader.title : cfg.follow;
    const items = cfg.presets
      .map((p) => `<li><button class="item${!this._tplId && p.id === this._selId ? " on" : ""}" data-kind="preset" data-id="${esc(p.id)}">
        <span>${p.readonly ? "&#128274; " : ""}${esc(p.name)}</span>${p.id === cfg.active ? `<span class="badge">${esc(t("active_badge"))}</span>` : ""}</button></li>`)
      .join("");
    const tplItems = this._templates()
      .map((x) => `<li><button class="item${x.id === this._tplId ? " on" : ""}" data-kind="tpl" data-id="${esc(x.id)}">
        <span>${esc(x.name)}</span><span class="badge tpl">${esc(t("template"))}</span></button></li>`)
      .join("");
    const locked = this._locked();
    const effName = cfg.effective_active_name;
    const hint = following
      ? t("follows_lib_hint").replace("{lamp}", () => leaderName).replace(" «{preset}»", () => (effName ? ` «${effName}»` : ""))
      : cur && cur.readonly
        ? t("readonly_hint")
        : cur && cur.template
          ? t("template_hint")
          : "";

    root.innerHTML = `<style>${STYLE}</style>
      <div class="layout">
        <section id="lib"><h2>${esc(t("lib_title"))}</h2>
          ${following ? `<div class="hint" id="follow-hint">${esc(hint)}</div>` : ""}
          <ul class="lib">${items}</ul>
          ${tplItems ? `<h3 id="tpl-title">${esc(t("lib_templates"))}</h3><ul class="lib" id="tpl-list">${tplItems}</ul>` : ""}
        </section>
        <section id="editor">
          ${cur ? `<div class="row"><h2>${esc(cur.name)}</h2>${cur.template ? `<span class="badge tpl">${esc(t("template"))}</span>` : cur.readonly ? "<span>&#128274;</span>" : ""}
              ${cur.id === cfg.active ? `<span class="badge">${esc(t("active_badge"))}</span>` : ""}</div>
            <div class="row">
              <button id="activate" class="secondary"${cur.template ? " hidden" : ""}${following || cur.id === cfg.active || this._busy ? " disabled" : ""}>${esc(t("activate"))}</button>
              <button id="rename" class="secondary"${cur.template ? " hidden" : ""}${following || cur.readonly ? " disabled" : ""}>${esc(t("rename"))}</button>
              <button id="delete" class="secondary"${cur.template ? " hidden" : ""}${following || cur.readonly ? " disabled" : ""}>${esc(t("delete"))}</button>
            </div>
            ${this._renderPrompt(t)}
            ${!following && hint ? `<div class="hint" id="ro-hint">${esc(hint)}</div>` : ""}
            <div class="hint" id="dirty-hint" hidden>${esc(t("dirty_hint"))}</div>
            <div class="row seg" role="group">
              <button id="v-curve" class="secondary${this._view === "curve" ? " on" : ""}">${esc(t("view_curve"))}</button>
              <button id="v-table" class="secondary${this._view === "table" ? " on" : ""}">${esc(t("view_table"))}</button>
            </div>
            <div id="slot"></div>
            <div class="row">
              <button id="save">${esc(t("save_new"))}</button>
              <button id="save-act" class="secondary">${esc(t("save_new_activate"))}</button>
              <span class="error" id="err" hidden></span><span class="ok" id="ok" hidden></span>
            </div>`
            : `<div class="hint">${esc(t("no_presets"))}</div>`}
        </section>
      </div>`;
    this._wire(cur, locked);
    this._refresh();
    if (this._scrollPrompt) {
      this._scrollPrompt = false;
      const prompt = root.getElementById("prompt");
      if (prompt) {
        prompt.scrollIntoView({ block: "center" });
        const input = root.getElementById("pname");
        if (input) input.focus({ preventScroll: true });
      }
    }
  }

  _renderPrompt(t) {
    const p = this._prompt;
    if (!p) return "";
    if (p.kind === "discard") {
      return `<div class="prompt" id="prompt" role="alert"><span>${esc(t("unsaved_prompt"))}</span>
        <button id="p-ok">${esc(t("discard"))}</button><button id="p-cancel" class="secondary">${esc(t("stay"))}</button></div>`;
    }
    if (p.kind === "delete") {
      const cur = this._current();
      return `<div class="prompt" id="prompt" role="alert"><span>${esc(t("delete_confirm").replace("{name}", () => (cur ? cur.name : "")))}</span>
        <button id="p-ok">${esc(t("delete"))}</button><button id="p-cancel" class="secondary">${esc(t("cancel"))}</button></div>`;
    }
    const label = { saveas: "prompt_save_new", rename: "prompt_rename" }[p.kind];
    return `<div class="prompt" id="prompt"><label for="pname">${esc(t(label))}</label>
      <input type="text" id="pname" value="${esc(this._value)}" maxlength="${MAX_NAME}">
      <button id="p-ok">${esc(t("ok"))}</button><button id="p-cancel" class="secondary">${esc(t("cancel"))}</button></div>`;
  }

  /** Cheap update: validation, button states, messages. Keeps the editor elements. */
  _refresh() {
    const root = this.shadowRoot;
    const t = this._t();
    const save = root.getElementById("save");
    if (!save || !this._points) return;
    const res = validate(this._points);
    const locked = this._locked();
    const dirty = this.isDirty();
    const cur = this._current();
    // Saving always creates a new entry: a template can be added as-is, an own preset only once edited.
    const canSave = !locked && res.ok && !this._busy && (dirty || !!cur.template);
    save.disabled = root.getElementById("save-act").disabled = !canSave;
    save.hidden = root.getElementById("save-act").hidden = locked;
    root.getElementById("dirty-hint").hidden = locked || !dirty;
    const pok = root.getElementById("p-ok");
    if (pok) pok.disabled = this._busy;
    if (this._editor) this._editor.setError(res.ok ? null : res.index);
    const err = root.getElementById("err");
    const msg = this._error ? `${t("error_prefix")}: ${this._error}` : !res.ok && !locked ? this._validationText(res, t) : "";
    err.hidden = !msg;
    err.textContent = msg;
    const ok = root.getElementById("ok");
    ok.hidden = !this._status || !!msg;
    ok.textContent = this._status ? t(this._status) : "";
  }

  _wire(cur, locked) {
    const root = this.shadowRoot;
    for (const b of root.querySelectorAll("button.item")) b.addEventListener("click", () => this._selectEntry(b.dataset.kind, b.dataset.id));
    if (!cur) return;
    root.getElementById("activate").addEventListener("click", () => this._activate());
    // Rename keeps the draft (it does not touch the points).
    root.getElementById("rename").addEventListener("click", () => this._showPrompt({ kind: "rename" }, cur.name));
    root.getElementById("delete").addEventListener("click", () => this._guard(() => this._showPrompt({ kind: "delete" })));
    const ok = root.getElementById("p-ok");
    if (ok) {
      ok.addEventListener("click", () => this._confirmPrompt());
      root.getElementById("p-cancel").addEventListener("click", () => { this._prompt = null; this._render(); });
    }
    const name = root.getElementById("pname");
    if (name) {
      name.addEventListener("input", () => { this._value = name.value; });
      name.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") this._confirmPrompt();
        if (ev.key === "Escape") { this._prompt = null; this._render(); }
      });
    }
    root.getElementById("v-curve").addEventListener("click", () => this._setView("curve"));
    root.getElementById("v-table").addEventListener("click", () => this._setView("table"));
    root.getElementById("save").addEventListener("click", () => this._save(false));
    root.getElementById("save-act").addEventListener("click", () => this._save(true));

    const ed = document.createElement(this._view === "curve" ? "ai-prime-curve-editor" : "ai-prime-table-editor");
    ed.lang = this._hass && this._hass.language;
    ed.readonly = locked;
    if (this._view === "curve") ed._sel = this._sel;
    ed.addEventListener("points-change", (ev) => this._onPoints(ev.detail.points));
    ed.addEventListener("select-change", (ev) => { this._sel = ev.detail.index; });
    root.getElementById("slot").appendChild(ed);
    ed.points = this._points;
    this._editor = ed;
  }

  _setView(view) {
    if (view === this._view) return;
    this._view = view;
    this._render();
  }
}

if (!customElements.get("ai-prime-tab-presets")) customElements.define("ai-prime-tab-presets", AiPrimeTabPresets);
