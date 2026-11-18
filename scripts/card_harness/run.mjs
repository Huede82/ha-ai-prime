// Dev-only browser harness: serves the card modules, drives them in Chromium and
// checks the main flows. Run: node scripts/card_harness/run.mjs
// Extend by adding checks to `scenarios` (Tasks 8/9 add their tabs there).

import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { launchOptions } from "./chromium.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(here, "../../custom_components/ai_prime/frontend");
const OUT = join(here, "out");
const TYPES = { ".js": "text/javascript", ".mjs": "text/javascript", ".html": "text/html" };

function serve() {
  const server = createServer(async (req, res) => {
    const path = new URL(req.url, "http://x").pathname;
    let file = null;
    if (path === "/harness.html") file = join(here, "harness.html");
    else if (path.startsWith("/frontend/")) {
      const rel = normalize(path.slice("/frontend/".length));
      if (!rel.startsWith("..")) file = join(FRONTEND, rel);
    }
    try {
      if (!file) throw new Error("not found");
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream", "cache-control": "no-store" });
      res.end(body);
    } catch {
      res.writeHead(404).end("not found");
    }
  });
  return new Promise((ok) => server.listen(0, "127.0.0.1", () => ok(server)));
}

const failures = [];
const check = (cond, msg) => {
  if (!cond) failures.push(msg);
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
};

const VIEWPORTS = [
  { name: "mobile", width: 390, height: 844 },
  { name: "desktop", width: 1280, height: 900 },
];

async function openPage(browser, base, vp, query = "") {
  const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
  const problems = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("requestfailed", (r) => problems.push(`requestfailed: ${r.url()}`));
  await page.goto(`${base}/harness.html${query}`);
  await page.waitForFunction(() => window.__ready === true);
  return { page, problems };
}

const wsCalls = (page, type) => page.evaluate((t) => window.__ws.filter((m) => m.type === t), type);

/** Orte flow shared by both modes. `mapMode` fires value-changed on the fake ha-selector. */
async function orteScenario(page, vp, label, mapMode) {
  const tag = `${label}/${vp.name}`;
  await page.waitForSelector("ai-prime-card #bri");
  check(await page.locator("ai-prime-card .title").textContent() === "Lamp 1", `${tag}: card renders title`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-card.png`) });

  await page.click("ai-prime-card #setup");
  const overlay = page.locator("ai-prime-studio .overlay");
  await overlay.waitFor();
  const box = await overlay.boundingBox();
  check(box && box.width >= vp.width - 1 && box.height >= vp.height - 1 && box.x <= 0 && box.y <= 0, `${tag}: overlay covers viewport`);

  await page.waitForSelector("ai-prime-tab-locations section.block");
  check(await page.locator("ai-prime-tab-locations section.block").count() === 3, `${tag}: three blocks`);
  await page.waitForFunction(() => window.__deep('ai-prime-tab-locations [data-info="sun"]')?.textContent.includes("07:48"));
  const infoText = await page.locator("ai-prime-tab-locations .info").allTextContents();
  check(infoText[0].includes("40 %") && infoText[1].includes("07:48") && infoText[2].includes("62 %"), `${tag}: live info (weather/sun/moon)`);
  const hasSel = await page.locator("ai-prime-tab-locations ha-selector").count();
  check(mapMode ? hasSel === 3 : hasSel === 0 && await page.locator("ai-prime-tab-locations input[type=number]").count() === 6,
    `${tag}: ${mapMode ? "ha-selector map pickers" : "lat/lon fallback inputs"}`);
  check(await page.locator("ai-prime-tab-locations #follow").count() === 1, `${tag}: follow dropdown`);
  check(await page.locator("ai-prime-tab-locations #save").isDisabled(), `${tag}: save disabled while clean`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-orte.png`) });

  // Geocode search in the sun block, pick the first result.
  await page.fill('ai-prime-tab-locations [data-q="sun"]', "Bern");
  await page.click('ai-prime-tab-locations [data-search="sun"]');
  await page.waitForSelector('ai-prime-tab-locations [data-pick="sun:0"]');
  check(await page.locator('ai-prime-tab-locations [data-pick^="sun:"]').count() === 2, `${tag}: geocode results rendered`);
  const geo = await wsCalls(page, "ai_prime/geocode");
  check(geo.length === 1 && geo[0].query === "Bern", `${tag}: geocode called with query`);
  await page.click('ai-prime-tab-locations [data-pick="sun:0"]');

  // Moon: change time mode, and set weather by map/coordinates.
  await page.check('ai-prime-tab-locations input[name="moon_mode"][value="real"]');
  if (mapMode) {
    await page.evaluate(() => {
      const sel = window.__deep('ai-prime-tab-locations [data-picker="weather"] ha-selector');
      sel.dispatchEvent(new CustomEvent("value-changed", { detail: { value: { latitude: 40.5, longitude: -3.25 } }, bubbles: true }));
    });
  } else {
    await page.fill('ai-prime-tab-locations [data-lat="weather"]', "40.5");
    await page.fill('ai-prime-tab-locations [data-lon="weather"]', "-3.25");
    await page.locator('ai-prime-tab-locations [data-lon="weather"]').blur();
  }
  check(await page.locator("ai-prime-tab-locations #save").isEnabled(), `${tag}: save enabled when dirty`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-orte-edited.png`) });

  // Dirty guard: closing shows the inline prompt; "stay" keeps the overlay.
  await page.click("ai-prime-studio #close");
  check(await page.locator("ai-prime-studio #prompt").isVisible(), `${tag}: dirty prompt on close`);
  await page.click("ai-prime-studio #stay");
  check(await page.locator("ai-prime-studio .overlay").count() === 1 && await page.locator("ai-prime-studio #prompt").isHidden(), `${tag}: stay keeps overlay`);

  await page.click("ai-prime-tab-locations #save");
  await page.waitForSelector("ai-prime-tab-locations #ok:not([hidden])");
  const sets = await wsCalls(page, "ai_prime/locations/set");
  check(sets.length === 1, `${tag}: locations/set called once`);
  const expected = {
    weather_location: { latitude: 40.5, longitude: -3.25 },
    sun_location: { latitude: 46.948, longitude: 7.4474, name: "Bern" },
    moon_location: null,
    sun_mode: "anchored",
    moon_mode: "real",
  };
  check(JSON.stringify(sets[0] && sets[0].locations) === JSON.stringify(expected), `${tag}: locations/set payload ${JSON.stringify(sets[0] && sets[0].locations)}`);
  check(sets[0] && sets[0].entry_id === "entry1", `${tag}: payload entry_id`);
  check(await page.locator("ai-prime-tab-locations #save").isDisabled(), `${tag}: save disabled after saving`);

  // Server error shows inline.
  await page.evaluate(() => { window.__fail["ai_prime/locations/set"] = { code: "boom", message: "Server said no" }; });
  await page.check('ai-prime-tab-locations input[name="sun_mode"][value="real"]');
  await page.click("ai-prime-tab-locations #save");
  await page.waitForSelector("ai-prime-tab-locations #err:not([hidden])");
  check((await page.locator("ai-prime-tab-locations #err").textContent()).includes("Server said no"), `${tag}: error shown inline`);
  await page.evaluate(() => { window.__fail = {}; });

  // Follow while dirty: inline prompt; "stay" keeps edits and sends nothing.
  await page.selectOption("ai-prime-tab-locations #follow", "entry2");
  await page.waitForSelector("ai-prime-tab-locations #fprompt");
  await page.click("ai-prime-tab-locations #fstay");
  check(await wsCalls(page, "ai_prime/follow/set").then((c) => c.length === 0) && await page.locator("ai-prime-tab-locations #save").isEnabled(), `${tag}: follow stay keeps edits`);
  await page.selectOption("ai-prime-tab-locations #follow", "entry2");
  await page.click("ai-prime-tab-locations #fdiscard");
  await page.waitForFunction(() => window.__ws.some((m) => m.type === "ai_prime/follow/set"));
  const follow = await wsCalls(page, "ai_prime/follow/set");
  check(follow[0].leader === "entry2" && follow[0].entry_id === "entry1", `${tag}: follow/set payload`);

  await page.check('ai-prime-tab-locations input[name="moon_mode"][value="real"]');
  // Tab switch to a placeholder pane while dirty prompts; discard then proceeds.
  await page.click('ai-prime-studio [data-tab="schedules"]');
  check(await page.locator("ai-prime-studio #prompt").isVisible(), `${tag}: dirty prompt on tab switch`);
  await page.click("ai-prime-studio #discard");
  check(await page.locator("ai-prime-tab-locations").count() === 0, `${tag}: discard switches tab`);
  await page.click("ai-prime-studio #close");
  await page.waitForSelector("ai-prime-studio", { state: "detached" });
  check(true, `${tag}: overlay closes`);
}

/** Farbschema tab: library actions, curve drag, table edit, validation, save payloads, follow read-only. */
async function presetsScenario(page, vp) {
  const tag = `presets/${vp.name}`;
  const T = "ai-prime-tab-presets";
  await page.waitForSelector("ai-prime-card #bri");
  await page.click("ai-prime-card #setup");
  await page.click('ai-prime-studio [data-tab="schedules"]');
  await page.waitForSelector(`${T} #lib button.item`);
  check((await page.locator('ai-prime-studio [data-tab="schedules"]').textContent()).trim() === "Farbschema", `${tag}: tab label Farbschema`);
  const OWN = `${T} ul.lib:not(#tpl-list) button.item`;
  const TPL = `${T} #tpl-list button.item`;
  const inView = (sel) => page.evaluate((q) => {
    const r = window.__deep(q).getBoundingClientRect();
    return r.top >= 0 && r.bottom <= window.innerHeight && r.height > 0;
  }, sel);
  check(await page.locator(OWN).count() === 2, `${tag}: library lists 2 own presets`);
  check(await page.locator(TPL).count() === 2 && (await page.locator(`${T} #tpl-title`).textContent()) === "Vorlagen", `${tag}: Vorlagen section lists 2 templates`);
  check(await page.locator(`${T} #tpl`).count() === 0 && await page.locator(`${T} #new`).count() === 0, `${tag}: no template select / new button`);
  check((await page.locator(TPL).nth(0).textContent()).includes("Vorlage"), `${tag}: template badge`);
  check(await page.locator(`${T} button.item.on .badge`).count() === 1, `${tag}: active badge on selected`);
  check((await page.locator(`${T} button.item`).nth(1).textContent()).includes("\u{1F512}"), `${tag}: lock on readonly`);
  check(await page.locator(`${T} ai-prime-curve-editor svg polyline`).count() === 5, `${tag}: 5 channel polylines`);
  check(await page.locator(`${T} ai-prime-curve-editor .h`).count() === 3, `${tag}: 3 handles`);
  const layout = await page.evaluate(() => {
    const a = window.__deep("ai-prime-tab-presets #lib").getBoundingClientRect();
    const b = window.__deep("ai-prime-tab-presets #editor").getBoundingClientRect();
    return { sideBySide: Math.abs(a.top - b.top) < 4 && b.left > a.right - 1 };
  });
  check(layout.sideBySide === (vp.width >= 900), `${tag}: layout ${vp.width >= 900 ? "side-by-side" : "stacked"}`);
  check(await page.locator(`${T} #save`).isDisabled() && await page.locator(`${T} #save-act`).isDisabled(), `${tag}: save buttons disabled while clean`);
  check((await page.locator(`${T} #save`).textContent()) === "Als neu speichern" && (await page.locator(`${T} #save-act`).textContent()) === "Als neu speichern & aktivieren", `${tag}: save button labels`);
  check(await page.locator(`${T} #dirty-hint`).isHidden() && await page.locator(`${T} #dup`).count() === 0, `${tag}: no dirty hint while clean, no duplicate button`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-curve.png`) });

  // Hit area >= 28 px and touch-action none on the SVG only.
  const hit = await page.evaluate(() => window.__deep("ai-prime-tab-presets ai-prime-curve-editor .h .hit").getBoundingClientRect().width);
  check(hit >= 28, `${tag}: handle hit area ${hit}px`);
  const ta = await page.evaluate(() => getComputedStyle(window.__deep("ai-prime-tab-presets ai-prime-curve-editor svg")).touchAction);
  check(ta === "none", `${tag}: touch-action none on svg`);

  // A tap 10 px off-centre with 1 px of jitter must not edit anything.
  const h1 = page.locator(`${T} ai-prime-curve-editor .h`).nth(1);
  const bb = await h1.boundingBox();
  const cx = bb.x + bb.width / 2, cy = bb.y + bb.height / 2;
  await page.mouse.move(cx + 10, cy + 6);
  await page.mouse.down();
  await page.mouse.move(cx + 11, cy + 6);
  await page.mouse.up();
  check(await page.locator(`${T} #save`).isDisabled(), `${tag}: off-centre tap with jitter leaves draft clean`);
  // Drag handle 1 (12:00, 50 %): right + up with pointer events.
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 40, cy - 20, { steps: 5 });
  await page.mouse.up();
  check(await page.locator(`${T} #save`).isEnabled(), `${tag}: drag makes draft dirty`);
  check(await page.locator(`${T} #dirty-hint`).isVisible() && (await page.locator(`${T} #dirty-hint`).textContent()).includes("neues Farbschema"), `${tag}: dirty hint shown`);
  const timeAfter = await page.inputValue(`${T} ai-prime-curve-editor #ptime`);
  check(timeAfter > "12:00", `${tag}: drag moved time (${timeAfter})`);
  const blueAfter = Number(await page.inputValue(`${T} ai-prime-curve-editor #s_blue`));
  check(blueAfter > 50, `${tag}: vertical drag scaled value (${blueAfter})`);
  // Multiplicative: all channels scaled by the same factor (they all started equal here).
  const greenAfter = Number(await page.inputValue(`${T} ai-prime-curve-editor #s_green`));
  check(greenAfter === blueAfter, `${tag}: channels scaled together`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-curve-drag.png`) });

  // Slider edit, add point, then delete the added one.
  await page.locator(`${T} ai-prime-curve-editor #s_warm_white`).fill("12.3");
  await page.click(`${T} ai-prime-curve-editor #add`);
  check(await page.locator(`${T} ai-prime-curve-editor .h`).count() === 4, `${tag}: + adds a point`);
  await page.click(`${T} ai-prime-curve-editor #del`);
  check(await page.locator(`${T} ai-prime-curve-editor .h`).count() === 3, `${tag}: trash deletes the point`);

  // Dirty guard: switching preset prompts inline; stay keeps edits.
  await page.click(`${T} button.item >> nth=1`);
  check(await page.locator(`${T} #prompt`).isVisible(), `${tag}: dirty prompt on preset switch`);
  await page.click(`${T} #p-cancel`);
  check(await page.locator(`${T} button.item.on`).textContent().then((x) => x.includes("Standard")) && await page.locator(`${T} #save`).isEnabled(), `${tag}: stay keeps selection and edits`);

  // Table view shows the same in-memory points; edit a value to something invalid.
  await page.click(`${T} #v-table`);
  check(await page.locator(`${T} ai-prime-table-editor tbody tr`).count() === 3, `${tag}: table has 3 rows`);
  check(await page.locator(`${T} #save`).isEnabled(), `${tag}: dirty survives view toggle`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-table.png`) });
  const row2 = `${T} ai-prime-table-editor tbody tr:nth-child(2)`;
  await page.fill(`${row2} [data-c="blue"]`, "150");
  await page.locator(`${row2} [data-c="blue"]`).blur();
  check(await page.locator(`${T} #err`).isVisible() && (await page.locator(`${T} #err`).textContent()).includes("2"), `${tag}: validation error shown with row`);
  check(await page.locator(`${row2}.bad`).count() === 1, `${tag}: invalid row highlighted`);
  check(await page.locator(`${T} #save`).isDisabled() && await page.locator(`${T} #save-act`).isDisabled(), `${tag}: save blocked while invalid`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-invalid.png`) });
  // Bad time text.
  await page.fill(`${row2} [data-c="blue"]`, "40");
  await page.locator(`${row2} [data-c="blue"]`).blur();
  await page.fill(`${row2} .time`, "25:99");
  await page.locator(`${row2} .time`).blur();
  check(await page.locator(`${row2}.bad`).count() === 1 && await page.locator(`${T} #save`).isDisabled(), `${tag}: bad time flagged`);
  await page.fill(`${row2} .time`, "13:00");
  await page.locator(`${row2} .time`).blur();
  check(await page.locator(`${T} #err`).isHidden() && await page.locator(`${T} #save`).isEnabled(), `${tag}: fixed row clears error`);

  // Save as new (not activating): name prompt prefilled, payload without preset_id.
  await page.click(`${T} #save`);
  check(await page.inputValue(`${T} #pname`) === "Standard 2", `${tag}: save-as-new name suggestion`);
  check(await inView(`${T} #prompt`), `${tag}: save prompt inside viewport`);
  check(await page.evaluate(() => window.__deep("ai-prime-tab-presets").shadowRoot.activeElement?.id === "pname"), `${tag}: name input focused`);
  await page.click(`${T} #p-ok`);
  await page.waitForSelector(`${T} #ok:not([hidden])`);
  let saves = await wsCalls(page, "ai_prime/preset/save");
  const s0 = saves[saves.length - 1];
  check(s0.preset_id === undefined && s0.name === "Standard 2" && s0.activate === false && s0.entry_id === "entry1", `${tag}: save payload header`);
  check(s0.points.length === 3 && s0.points[1].t === 780 && s0.points[1].v.blue === 40 && Object.keys(s0.points[1].v).join() === "blue,green,deep_red,warm_white,cool_white", `${tag}: save payload points`);
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Standard 2");
  check(await page.locator(`${T} #save`).isDisabled() && await page.locator(OWN).count() === 3, `${tag}: new preset selected, clean, original untouched`);
  check(await page.evaluate(() => window.__config.presets.find((x) => x.id === "p1").points.length === 3 && window.__config.presets.find((x) => x.id === "p1").points[1].t === 720), `${tag}: existing preset not overwritten`);

  // Server-side error display (invalid_points).
  await page.fill(`${row2} [data-c="green"]`, "33");
  await page.locator(`${row2} [data-c="green"]`).blur();
  await page.evaluate(() => { window.__fail["ai_prime/preset/save"] = { code: "invalid_points", message: "Server rejected the points" }; });
  await page.click(`${T} #save`);
  check(await page.inputValue(`${T} #pname`) === "Standard 3", `${tag}: suggestion skips taken names`);
  await page.click(`${T} #p-ok`);
  await page.waitForSelector(`${T} #err:not([hidden])`);
  check((await page.locator(`${T} #err`).textContent()).includes("Server rejected"), `${tag}: server error shown`);
  await page.evaluate(() => { window.__fail = {}; });
  // Save as new & activate.
  await page.click(`${T} #save-act`);
  await page.click(`${T} #p-ok`);
  await page.waitForSelector(`${T} #ok:not([hidden])`);
  saves = await wsCalls(page, "ai_prime/preset/save");
  check(saves[saves.length - 1].activate === true && saves[saves.length - 1].preset_id === undefined && saves[saves.length - 1].name === "Standard 3", `${tag}: save as new & activate payload`);
  check(saves.every((x) => x.preset_id === undefined), `${tag}: editor never sends preset_id`);

  // Readonly original: hint, rename/delete disabled; editing is allowed and saves as new.
  await page.click(`${T} button.item[data-id="original"]`);
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #ro-hint"));
  check((await page.locator(`${T} #ro-hint`).textContent()).includes("neues Farbschema"), `${tag}: readonly hint`);
  check(await page.locator(`${T} #save`).isDisabled() && await page.locator(`${T} #rename`).isDisabled() && await page.locator(`${T} #delete`).isDisabled(), `${tag}: readonly clean: save disabled, rename/delete disabled`);
  await page.click(`${T} #v-curve`);
  await page.waitForSelector(`${T} ai-prime-curve-editor .h`);
  await page.waitForTimeout(200); // let the reload re-render settle
  await page.locator(`${T} ai-prime-curve-editor .h`).nth(0).scrollIntoViewIfNeeded();
  const hBefore = await page.locator(`${T} ai-prime-curve-editor .h`).nth(0).boundingBox();
  const hx = hBefore.x + hBefore.width / 2, hy = hBefore.y + hBefore.height / 2;
  await page.mouse.move(hx, hy);
  await page.mouse.down();
  await page.mouse.move(hx + 60, hy - 30, { steps: 4 });
  await page.mouse.up();
  check(await page.locator(`${T} #save`).isEnabled() && await page.evaluate(() => window.__deep("ai-prime-tab-presets").isDirty() === true), `${tag}: readonly source can be edited, save-as-new enabled`);
  await page.screenshot({ path: join(OUT, `${tag.replace("/", "-")}-readonly.png`) });
  await page.click(`${T} #save`);
  check(await page.inputValue(`${T} #pname`) === "Original (Lampe) 2", `${tag}: suggestion from read-only name`);
  await page.fill(`${T} #pname`, "Kopie <b>");
  await page.click(`${T} #p-ok`);
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Kopie <b>");
  saves = await wsCalls(page, "ai_prime/preset/save");
  const dup = saves[saves.length - 1];
  check(dup.preset_id === undefined && dup.name === "Kopie <b>" && dup.points.length === 2 && dup.activate === false, `${tag}: save-as-new from original payload`);
  check(await page.locator(`${T} #editor h2 b`).count() === 0, `${tag}: names are escaped`);
  check(await page.locator(OWN).count() === 5, `${tag}: library has the copy`);

  // Rename.
  await page.click(`${T} #rename`);
  check(await inView(`${T} #prompt`), `${tag}: rename prompt inside viewport`);
  await page.fill(`${T} #pname`, "Umbenannt");
  await page.press(`${T} #pname`, "Enter");
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Umbenannt");
  const ren = await wsCalls(page, "ai_prime/preset/rename");
  check(ren.length === 1 && ren[0].preset_id === "n5" && ren[0].name === "Umbenannt", `${tag}: rename payload`);

  // Select a template: editor shows its points; clean template can be saved as new right away.
  await page.click(`${T} #tpl-list button.item[data-id="natur"]`);
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Natur");
  check(await page.locator(`${T} #tpl-list button.item.on`).count() === 1 && await page.locator(`${T} ai-prime-curve-editor .h`).count() === 3, `${tag}: template shown in editor`);
  check(await page.locator(`${T} #save`).isEnabled() && await page.locator(`${T} #save-act`).isEnabled() && await page.locator(`${T} #activate`).isHidden(), `${tag}: template: save buttons enabled without edits`);
  await page.click(`${T} #save`);
  check(await page.inputValue(`${T} #pname`) === "Natur 2", `${tag}: template name suggestion`);
  await page.click(`${T} #p-cancel`);
  await page.waitForTimeout(200);
  await page.locator(`${T} ai-prime-curve-editor .h`).nth(1).scrollIntoViewIfNeeded();
  const th = await page.locator(`${T} ai-prime-curve-editor .h`).nth(1).boundingBox();
  await page.mouse.move(th.x + th.width / 2, th.y + th.height / 2);
  await page.mouse.down();
  await page.mouse.move(th.x + th.width / 2 + 40, th.y + th.height / 2 - 20, { steps: 5 });
  await page.mouse.up();
  check(await page.locator(`${T} #dirty-hint`).isVisible(), `${tag}: template edit shows dirty hint`);
  await page.click(`${T} #save-act`);
  check(await page.inputValue(`${T} #pname`) === "Natur 2" && await inView(`${T} #prompt`), `${tag}: save & activate prompt prefilled and in viewport`);
  await page.fill(`${T} #pname`, "Meins");
  const before = (await wsCalls(page, "ai_prime/preset/save")).length;
  await page.evaluate(() => { const b = window.__deep("ai-prime-tab-presets #p-ok"); b.click(); b.click(); });
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Meins");
  check((await wsCalls(page, "ai_prime/preset/save")).length === before + 1, `${tag}: double click on OK saves once`);
  saves = await wsCalls(page, "ai_prime/preset/save");
  const tsv = saves[saves.length - 1];
  check(tsv.name === "Meins" && tsv.activate === true && tsv.preset_id === undefined && tsv.points.length === 3 && tsv.points[1].t > 600, `${tag}: template save payload`);
  check(await page.locator(`${T} #editor .badge`).count() === 1 && await page.locator(OWN).count() === 6, `${tag}: new preset selected and active`);

  // Delete: confirmation is inline and visible; the active preset is refused by the server.
  await page.click(`${T} #delete`);
  check(await page.locator(`${T} #prompt`).isVisible() && await inView(`${T} #prompt`), `${tag}: inline delete confirmation in viewport`);
  await page.click(`${T} #p-ok`);
  await page.waitForSelector(`${T} #err:not([hidden])`);
  check((await page.locator(`${T} #err`).textContent()).includes("cannot be deleted"), `${tag}: delete of active preset refused (server error shown)`);
  await page.click(`${T} button.item[data-id="n5"]`);
  await page.click(`${T} #delete`);
  await page.click(`${T} #p-ok`);
  await page.waitForFunction(() => window.__ws.some((m) => m.type === "ai_prime/preset/delete" && m.preset_id === "n5"));
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets").shadowRoot.querySelectorAll("ul.lib:not(#tpl-list) button.item").length === 5);
  check(true, `${tag}: deleted preset leaves the library`);

  // Activate a non-active preset.
  await page.click(`${T} button.item[data-id="original"]`);
  await page.click(`${T} #activate`);
  await page.waitForFunction(() => window.__ws.some((m) => m.type === "ai_prime/preset/activate"));
  const act = await wsCalls(page, "ai_prime/preset/activate");
  check(act[0].preset_id === "original", `${tag}: activate payload`);

  // Following a leader: read-only library with hint, activation disabled.
  await page.evaluate(() => {
    window.__config.follow = "entry2";
    window.__config.effective_active_name = "Leader Plan"; // lives in the leader's library, not ours
    window.__deep("ai-prime-tab-presets").dispatchEvent(new CustomEvent("studio-reload", { bubbles: true, composed: true }));
  });
  await page.waitForSelector(`${T} #follow-hint`);
  check((await page.locator(`${T} #follow-hint`).textContent()).includes("Lamp 2") && (await page.locator(`${T} #follow-hint`).textContent()).includes("\u00abLeader Plan\u00bb"), `${tag}: follow hint names leader and active schedule`);
  check(await page.locator(`${T} #activate`).isDisabled() && await page.locator(`${T} #save`).isHidden() && await page.locator(`${T} #save-act`).isHidden(), `${tag}: following disables editing and activation`);
  await page.click(`${T} #tpl-list button.item[data-id="natur"]`);
  await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Natur");
  check(await page.locator(`${T} #save`).isHidden() && await page.locator(`${T} #save-act`).isHidden(), `${tag}: templates not savable while following`);
  await page.evaluate(() => { window.__config.follow = null; window.__config.effective_active_name = "Standard"; });
}

/** Vorschau tab: today with weather, other date without, markers, now-line only for today. */
async function previewScenario(page, vp) {
  const tag = `preview/${vp.name}`;
  const P = "ai-prime-tab-preview";
  await page.waitForSelector("ai-prime-card #setup");
  await page.click("ai-prime-card #setup");
  await page.locator("ai-prime-studio .overlay").waitFor();
  await page.click('ai-prime-studio [data-tab="preview"]');
  await page.waitForSelector(`${P} #chart`);
  const calls = await wsCalls(page, "ai_prime/preview");
  const today = await page.evaluate(() => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Zurich" }).format(new Date()));
  check(calls.at(-1).date === today, `${tag}: first request is for today`);
  check(await page.locator(`${P} polyline.eff`).count() === 5 && await page.locator(`${P} polyline.base`).count() === 5, `${tag}: thin and thick line per channel`);
  check(await page.locator(`${P} line.mk`).count() === 4 && (await page.locator(`${P} .mk-label`).first().textContent()).includes("Monduntergang"), `${tag}: markers with labels`);
  check(await page.locator(`${P} line.now`).count() === 1, `${tag}: now-line for today`);
  const nowX = await page.locator(`${P} line.now`).getAttribute("x1");
  const expect = await page.evaluate(() => {
    const p = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Zurich", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date());
    const g = (t) => Number(p.find((x) => x.type === t).value);
    return 34 + ((g("hour") * 60 + g("minute")) / 1440) * (800 - 34 - 8);
  });
  check(Math.abs(Number(nowX) - expect) < 1.5, `${tag}: now-line uses HA time zone (${nowX} vs ${expect.toFixed(1)})`);
  const times = await page.locator(`${P} #times`).textContent();
  check(times.includes("07:48") && times.includes("40 %") && times.includes("62 %") && times.includes("00:00–05:21"), `${tag}: times list shows sun, moon, weather`);
  await page.screenshot({ path: join(OUT, `preview-${vp.name}-today.png`) });

  await page.click(`${P} #d-tomorrow`);
  await page.waitForFunction(() => !window.__deep("ai-prime-tab-preview #times")?.textContent.includes("Wolken") && window.__deep("ai-prime-tab-preview #chart"));
  const after = await wsCalls(page, "ai_prime/preview");
  check(after.at(-1).date !== today, `${tag}: tomorrow requests another date`);
  check(await page.locator(`${P} line.now`).count() === 0, `${tag}: no now-line for another date`);
  check(await page.locator(`${P} #no-weather`).count() === 1, `${tag}: no-weather note`);
  check(await page.locator(`${P} line.mk`).count() === 4, `${tag}: markers on other date`);
  await page.screenshot({ path: join(OUT, `preview-${vp.name}-tomorrow.png`) });

  await page.fill(`${P} #d-input`, "2027-03-01");
  await page.dispatchEvent(`${P} #d-input`, "change");
  await page.waitForFunction(() => window.__ws.filter((m) => m.type === "ai_prime/preview").at(-1).date === "2027-03-01");
  check(true, `${tag}: date input requests chosen date`);
  await page.click(`${P} #d-today`);
  await page.waitForSelector(`${P} line.now`, { state: "attached" });
  check(true, `${tag}: back to today restores now-line`);
}

const browser = await chromium.launch(launchOptions(chromium)); // PW_CHROMIUM overrides
const server = await serve();
const base = `http://127.0.0.1:${server.address().port}`;
await mkdir(OUT, { recursive: true });
try {
  for (const vp of VIEWPORTS) {
    const { page, problems } = await openPage(browser, base, vp);
    await orteScenario(page, vp, "fallback", false);
    check(problems.length === 0, `fallback/${vp.name}: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
  for (const vp of VIEWPORTS) {
    const { page, problems } = await openPage(browser, base, vp);
    await presetsScenario(page, vp);
    check(problems.length === 0, `presets/${vp.name}: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
  for (const vp of VIEWPORTS) {
    const { page, problems } = await openPage(browser, base, vp);
    await previewScenario(page, vp);
    check(problems.length === 0, `preview/${vp.name}: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
  {
    // Card inside a transformed/contained ancestor: the body-mounted overlay must still cover the viewport.
    const vp = VIEWPORTS[0];
    const { page, problems } = await openPage(browser, base, vp, "?wrap=1");
    await page.click("ai-prime-card #setup");
    await page.locator("ai-prime-studio .overlay").waitFor();
    const box = await page.locator("ai-prime-studio .overlay").boundingBox();
    check(box && box.width >= vp.width - 1 && box.height >= vp.height - 1 && box.x <= 0 && box.y <= 0, "wrap: overlay covers viewport inside transformed ancestor");
    const top = await page.evaluate(() => { const e = document.elementFromPoint(195, 400); return e && e.tagName.toLowerCase(); });
    check(top === "ai-prime-studio", `wrap: overlay is topmost (${top})`);
    // Card rebuild (language change) must not destroy the open studio.
    await page.evaluate(() => { const h = { ...window.__hass, language: "en" }; document.getElementById("card").hass = h; });
    check(await page.locator("ai-prime-studio").count() === 1, "wrap: studio survives card rebuild");
    check(problems.length === 0, `wrap: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
  {
    // Lovelace re-parents cards (masonry columns on resize): the open studio and its draft survive.
    const vp = VIEWPORTS[1];
    const T = "ai-prime-tab-presets";
    const { page, problems } = await openPage(browser, base, vp);
    await page.waitForSelector("ai-prime-card #bri");
    await page.click("ai-prime-card #setup");
    await page.click('ai-prime-studio [data-tab="schedules"]');
    await page.waitForSelector(`${T} ai-prime-curve-editor #add`);
    await page.click(`${T} ai-prime-curve-editor #add`);
    check(await page.locator(`${T} #save`).isEnabled(), "reparent: draft dirty before move");
    await page.evaluate(() => {
      const card = document.getElementById("card");
      const column = document.createElement("div");
      document.body.appendChild(column);
      column.appendChild(card); // disconnect + connect in one task
    });
    await page.waitForTimeout(50);
    check(await page.locator("ai-prime-studio").count() === 1, "reparent: studio survives re-parenting");
    check(await page.locator(`${T} ai-prime-curve-editor .h`).count() === 4 && await page.locator(`${T} #save`).isEnabled(), "reparent: dirty draft survives");
    // The lamp refusing the schedule is shown translated; the save itself is kept.
    await page.evaluate(() => { window.__failAfter["ai_prime/preset/save"] = { code: "lamp_rejected", message: "Lamp 1" }; });
    await page.click(`${T} #save-act`);
    await page.click(`${T} #p-ok`);
    await page.waitForFunction(() => !window.__deep("ai-prime-tab-presets #err").hidden);
    const errText = await page.locator(`${T} #err`).textContent();
    check(errText.includes("Lampe hat das Farbschema abgelehnt"), `reparent: lamp_rejected shown translated (${errText})`);
    await page.waitForFunction(() => window.__deep("ai-prime-tab-presets #editor h2")?.textContent === "Standard 2");
    check(await page.locator(`${T} #save`).isDisabled(), "reparent: stored preset selected after lamp_rejected, draft clean");
    check((await page.locator(`${T} #err`).textContent()).includes("Farbschema"), "reparent: lamp_rejected error survives the reload");
    await page.evaluate(() => { window.__failAfter = {}; });
    // Really removing the card still closes the studio.
    await page.evaluate(() => document.getElementById("card").remove());
    await page.waitForSelector("ai-prime-studio", { state: "detached" });
    check(true, "reparent: removing the card removes the studio");
    check(problems.length === 0, `reparent: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
  {
    const vp = VIEWPORTS[1];
    const { page, problems } = await openPage(browser, base, vp, "?selector=1");
    await orteScenario(page, vp, "selector", true);
    check(problems.length === 0, `selector/${vp.name}: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
  {
    // Inside HA the studio must live in <home-assistant>'s shadow root so HA
    // components (map tile token via context) work; <body> is only a fallback.
    const vp = VIEWPORTS[1];
    const { page, problems } = await openPage(browser, base, vp);
    await page.evaluate(() => { const ha = document.createElement("home-assistant"); ha.attachShadow({ mode: "open" }); document.body.appendChild(ha); });
    await page.evaluate(() => window.__deep("ai-prime-card").shadowRoot.querySelector("#setup").click());
    await page.waitForTimeout(300);
    const where = await page.evaluate(() => ({
      inHa: !!document.querySelector("home-assistant").shadowRoot.querySelector("ai-prime-studio"),
      inBody: !!document.querySelector("body > ai-prime-studio"),
    }));
    check(where.inHa && !where.inBody, "mount: studio inside <home-assistant> shadow root when present");
    check(problems.length === 0, `mount: no console/page errors${problems.length ? ` (${problems.join("; ")})` : ""}`);
    await page.close();
  }
} catch (err) {
  failures.push(`exception: ${err.stack || err}`);
  console.log(`FAIL exception: ${err.stack || err}`);
} finally {
  await browser.close();
  server.close();
}
console.log(failures.length ? `\n${failures.length} failure(s)` : "\nall checks passed");
process.exit(failures.length ? 1 : 0);
