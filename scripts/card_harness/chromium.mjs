// Chromium for the dev scripts: PW_CHROMIUM wins, else Playwright's own browser.
// If Playwright's pinned revision is not installed, fall back to any installed
// headless shell in the Playwright cache (Linux layout) instead of failing.

import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export function launchOptions(chromium) {
  if (process.env.PW_CHROMIUM) return { executablePath: process.env.PW_CHROMIUM };
  if (existsSync(chromium.executablePath())) return {};
  const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(homedir(), ".cache", "ms-playwright");
  let dirs = [];
  try {
    dirs = readdirSync(cache).filter((d) => /^chromium_headless_shell-\d+$/.test(d));
  } catch {
    return {};
  }
  dirs.sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
  for (const d of dirs) {
    const exe = join(cache, d, "chrome-headless-shell-linux64", "chrome-headless-shell");
    if (existsSync(exe)) return { executablePath: exe };
  }
  return {};
}
