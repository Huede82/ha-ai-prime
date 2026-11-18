// Live smoke test of the AI Prime card + studio against a real Home Assistant. READ-ONLY:
// opens the studio and visits every tab, never saves, activates, deletes or follows.
//
//   HA_URL=http://ha:8123 HA_TOKEN=<long-lived token> \
//   [PW_CHROMIUM=/path/to/chromium] node scripts/studio_smoke.mjs /lovelace/0
//
// Playwright comes from the harness install: (cd scripts/card_harness && npm install).
// Screenshots go to scripts/card_harness/out/live-*.png. Exit 1 on any console error.

import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { launchOptions } from "./card_harness/chromium.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "card_harness", "package.json"))("playwright");
const OUT = join(here, "card_harness", "out");

const hassUrl = (process.env.HA_URL || "").replace(/\/+$/, "");
const token = process.env.HA_TOKEN;
const path = process.argv[2];
if (!hassUrl || !token || !path) {
  console.error("usage: HA_URL=... HA_TOKEN=... node scripts/studio_smoke.mjs <dashboard path, e.g. /lovelace/0>");
  process.exit(2);
}

const problems = [];
const browser = await chromium.launch(launchOptions(chromium)); // PW_CHROMIUM overrides
await mkdir(OUT, { recursive: true });
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addInitScript(
    ([url, tok]) => {
      localStorage.setItem("hassTokens", JSON.stringify({
        hassUrl: url, clientId: `${url}/`, access_token: tok, token_type: "Bearer",
        expires_in: 1e9, refresh_token: "x", expires: Date.now() + 1e12,
      }));
    },
    [hassUrl, token],
  );
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

  await page.goto(`${hassUrl}${path.startsWith("/") ? path : `/${path}`}`);
  // Find the card through shadow roots (poll: the dashboard renders lazily).
  const found = await page.waitForFunction(() => {
    const walk = (root) => {
      const hit = root.querySelector("ai-prime-card");
      if (hit) return hit;
      for (const el of root.querySelectorAll("*")) {
        if (el.shadowRoot) { const f = walk(el.shadowRoot); if (f) return f; }
      }
      return null;
    };
    window.__card = walk(document);
    return !!window.__card;
  }, null, { timeout: 60000 }).then(() => true, () => false);
  if (!found) throw new Error("ai-prime-card not found on the dashboard");

  await page.screenshot({ path: join(OUT, "live-card.png") });
  await page.evaluate(() => window.__card.shadowRoot.querySelector("#setup").click());
  await page.locator("ai-prime-studio .overlay").waitFor({ timeout: 15000 });
  for (const tab of ["locations", "schedules", "preview"]) {
    await page.click(`ai-prime-studio [data-tab="${tab}"]`);
    await page.waitForTimeout(2500); // let the tab load its websocket data
    await page.screenshot({ path: join(OUT, `live-${tab}.png`) });
    console.log(`visited tab ${tab}`);
  }
} catch (err) {
  problems.push(`exception: ${err.message}`);
} finally {
  await browser.close();
}
for (const p of problems) console.log(`ERROR ${p}`);
console.log(problems.length ? `${problems.length} problem(s)` : "smoke ok (no console errors)");
process.exit(problems.length ? 1 : 0);
