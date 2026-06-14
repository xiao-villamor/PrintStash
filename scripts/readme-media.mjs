// Generates README screenshots + navigation GIFs by driving the running app
// (http://localhost:3000) with Playwright. Re-run with:
//   node scripts/readme-media.mjs            # screenshots only
//   node scripts/readme-media.mjs --video    # also record videos for GIFs
//
// Requires the app running and the playwright package from frontend/.
import { mkdir, rm, rename } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Playwright is only installed in frontend/ (as @playwright/test); resolve from there.
const require = createRequire(path.join(ROOT, "frontend/package.json"));
const { chromium } = require("@playwright/test");
const OUT = path.join(ROOT, "screenshots");
const VIDEO_TMP = path.join(OUT, ".video-tmp");
const BASE = process.env.PS_BASE ?? "http://localhost:3000";
const USER = process.env.PS_USER ?? "admin";
const PASS = process.env.PS_PASS ?? "admin1234";
const WITH_VIDEO = process.argv.includes("--video");

const VW = 1440;
const VH = 900;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function forceDarkTheme(context) {
  await context.addInitScript(() => {
    try {
      localStorage.setItem("printstash.theme", "dark");
      document.documentElement.classList.add("dark");
    } catch {}
  });
}

// Replace any real endpoint URL shown in the page with a neutral local
// placeholder so screenshots never leak the user's domain. Scrubs only full
// http(s) URLs (Moonraker endpoints carry a scheme) to avoid mangling unrelated
// text like ".gcode" filenames or slicer version strings.
async function maskEndpoints(page) {
  await page.evaluate(() => {
    const REPLACEMENT = "https://moonraker.local";
    // Fresh regex per use — a shared /g/ regex carries lastIndex across calls.
    const scrub = (s) => (s || "").replace(/https?:\/\/[^\s"']+/gi, REPLACEMENT);

    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walk.nextNode())) {
      if (n.nodeValue) n.nodeValue = scrub(n.nodeValue);
    }
    document.querySelectorAll("a[href]").forEach((a) => {
      if (/^https?:/i.test(a.getAttribute("href") || "")) a.setAttribute("href", REPLACEMENT);
    });
    document.querySelectorAll("[title]").forEach((el) => {
      el.setAttribute("title", scrub(el.getAttribute("title")));
    });
    document.querySelectorAll("input").forEach((el) => {
      if (el.value) el.value = scrub(el.value);
    });
  });
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  if (page.url().includes("/login")) {
    await page.getByRole("textbox", { name: "Username" }).fill(USER);
    await page.getByRole("textbox", { name: "Password" }).fill(PASS);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL((u) => !u.toString().includes("/login"), { timeout: 15000 });
  }
  await page.waitForLoadState("networkidle");
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, name), scale: "css" });
  console.log("  ✓", name);
}

// Orbit the 3D canvas by dragging across it.
async function orbitCanvas(page, { steps = 30, dx = 6, dy = 2 } = {}) {
  const canvas = page.locator("canvas").first();
  await canvas.waitFor({ state: "visible", timeout: 15000 });
  const box = await canvas.boundingBox();
  if (!box) return;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  for (let i = 0; i < steps; i++) {
    await page.mouse.move(cx + dx * i, cy + Math.sin(i / 4) * dy * 4, { steps: 2 });
    await sleep(16);
  }
  await page.mouse.up();
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--use-gl=angle",
      "--use-angle=swiftshader",
      "--enable-webgl",
      "--ignore-gpu-blocklist",
    ],
  });

  // ---- Phase 1: screenshots (no video) ----
  const ctx = await browser.newContext({
    viewport: { width: VW, height: VH },
    deviceScaleFactor: 2,
  });
  await forceDarkTheme(ctx);
  const page = await ctx.newPage();
  await login(page);

  // 01 — asset grid: open a populated sub-collection so the grid is full of cards
  await page.goto(`${BASE}/?c=skadis/skadis-paper`, { waitUntil: "networkidle" });
  await sleep(1800);
  await shot(page, "01-asset-grid.png");

  // 02 — collections filter: select the "car" tag, sidebar tree narrows to the
  // matching model while the grid still shows the match (feature showcase).
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await sleep(800);
  const tag = page.getByRole("button", { name: /^car\s*\d/ }).first();
  if (await tag.count()) {
    await tag.click();
    await sleep(900);
    await shot(page, "02-collections-filter.png");
    await tag.click(); // clear
    await sleep(500);
  }

  // 03 — search
  const search = page.getByRole("textbox", { name: /Search PrintStash/ });
  if (await search.count()) {
    await search.click();
    await search.fill("skadis");
    await sleep(1200);
    await shot(page, "03-search.png");
    await search.fill("");
    await sleep(400);
  }

  // ---- model detail (id 4 has 3 revisions) ----
  await page.goto(`${BASE}/models/4`, { waitUntil: "networkidle" });
  await sleep(1500);

  // 04 — model overview
  await page.getByRole("button", { name: "Overview" }).click().catch(() => {});
  await sleep(600);
  await shot(page, "04-model-detail.png");

  // 05 — 3D viewer (X-Ray mode looks striking)
  await orbitCanvas(page, { steps: 18, dx: 7 });
  await page.getByRole("button", { name: "X-Ray" }).click().catch(() => {});
  await sleep(1000);
  await shot(page, "05-3d-viewer.png");
  await page.getByRole("button", { name: "Solid" }).click().catch(() => {});

  // 06 — gcode viewer
  const gcodeBtn = page.getByRole("button", { name: /^GCode$/ });
  if (await gcodeBtn.count()) {
    await gcodeBtn.click();
    await sleep(2500);
    await shot(page, "06-gcode-viewer.png");
    await page.getByRole("button", { name: /^3D$/ }).click().catch(() => {});
    await sleep(800);
  }

  // 07 — revisions tab (top: revision list)
  await page.getByRole("button", { name: /Revisions/ }).click().catch(() => {});
  await sleep(900);
  await shot(page, "07-revisions.png");

  // 09 — printer detail: the richest printer view (live status, temps, current
  // file, job history, diagnostics). Open the first printer and censor any real
  // endpoint/host so the README never leaks the user's domain.
  await page.goto(`${BASE}/printers`, { waitUntil: "networkidle" });
  await sleep(1000);
  const openLink = page.locator('a[href^="/printers/"]').first();
  const href = (await openLink.count()) ? await openLink.getAttribute("href") : null;
  if (href) {
    await page.goto(`${BASE}${href}`, { waitUntil: "networkidle" });
    await sleep(1600);
    await maskEndpoints(page);
    await sleep(200);
    await shot(page, "09-printers.png");

    // 17 — printer files: G-code inventory on the printer
    const filesTab = page.getByRole("button", { name: /^Files$/i }).first();
    if (await filesTab.count()) {
      await filesTab.click();
      await sleep(1400);
      await maskEndpoints(page);
      await sleep(200);
      await shot(page, "17-printer-files.png");
    }

    // 13 — printer diagnostics: capability matrix + health checks
    const diag = page.getByRole("button", { name: /Diagnostics/i }).first();
    if (await diag.count()) {
      await diag.click();
      await sleep(1200);
      await maskEndpoints(page);
      await sleep(200);
      await shot(page, "13-printer-diagnostics.png");
    }
  }

  // 10 — settings (Overview) + more sections
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await sleep(1200);
  await shot(page, "10-settings.png");

  const settingsSections = [
    ["Users & Access", "14-settings-access.png"],
    ["Storage", "15-settings-storage.png"],
    ["Design", "16-settings-design.png"],
  ];
  for (const [label, file] of settingsSections) {
    const tab = page.getByRole("button", { name: label }).first();
    if (await tab.count()) {
      await tab.click();
      await sleep(1100);
      await maskEndpoints(page); // precaution: scrub any S3 endpoint URLs
      await sleep(150);
      await shot(page, file);
    }
  }

  const storageState = await ctx.storageState();
  await ctx.close();

  // ---- Phase 2: videos → GIFs ----
  if (WITH_VIDEO) {
    await rm(VIDEO_TMP, { recursive: true, force: true });
    await mkdir(VIDEO_TMP, { recursive: true });
    await recordFlow(browser, storageState, "demo", demoTour);
    await recordFlow(browser, storageState, "compare", compareFlow);
    await recordFlow(browser, storageState, "filter", filterFlow);
    console.log("Videos recorded →", VIDEO_TMP);
  }

  await browser.close();
  console.log("Screenshots done →", OUT);
}

const VVW = 1280;
const VVH = 800;

async function recordFlow(browser, storageState, name, fn) {
  const ctx = await browser.newContext({
    viewport: { width: VVW, height: VVH },
    storageState,
    recordVideo: { dir: VIDEO_TMP, size: { width: VVW, height: VVH } },
  });
  await forceDarkTheme(ctx);
  const page = await ctx.newPage();
  try {
    await fn(page);
  } finally {
    const video = page.video();
    await ctx.close();
    if (video) {
      const src = await video.path();
      await rename(src, path.join(VIDEO_TMP, `${name}.webm`));
    }
    console.log("  ●", `${name}.webm`);
  }
}

// Navigate without the networkidle dead-time that bloats the recording.
async function goFast(page, url, ready) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  if (ready) await page.locator(ready).first().waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
}

// Hero tour: browse grid → open a model → orbit 3D → X-Ray → flip through tabs.
async function demoTour(page) {
  await goFast(page, `${BASE}/?c=skadis/skadis-paper`, "article");
  await sleep(1200);
  await page.mouse.wheel(0, 220);
  await sleep(700);
  await page.mouse.wheel(0, -220);
  await sleep(500);
  await goFast(page, `${BASE}/models/4`, "canvas");
  await sleep(1200);
  await orbitCanvas(page, { steps: 42, dx: 6, dy: 1.5 });
  await sleep(300);
  await page.getByRole("button", { name: "X-Ray" }).click().catch(() => {});
  await sleep(900);
  await orbitCanvas(page, { steps: 24, dx: -7 });
  await page.getByRole("button", { name: "Solid" }).click().catch(() => {});
  await sleep(500);
  await page.getByRole("button", { name: "Settings" }).click().catch(() => {});
  await sleep(1100);
  await page.getByRole("button", { name: /Revisions/ }).click().catch(() => {});
  await sleep(1200);
}

// Revision comparison: open Revisions, scroll to the diff, switch the compared rev.
async function compareFlow(page) {
  await goFast(page, `${BASE}/models/4`, "canvas");
  await sleep(900);
  await page.getByRole("button", { name: /Revisions/ }).click().catch(() => {});
  await sleep(900);
  const section = page.locator("section").filter({ has: page.getByText("Compare Revisions") }).first();
  await section.scrollIntoViewIfNeeded().catch(() => {});
  await sleep(1000);
  const selects = section.locator("select");
  if (await selects.count()) {
    const opts = await selects.first().locator("option").all();
    if (opts.length > 1) {
      const v = await opts[opts.length - 1].getAttribute("value");
      await selects.first().selectOption(v);
      await sleep(1200);
    }
  }
  await sleep(900);
}

// Filtering: clicking a tag narrows the collections tree (the new feature).
async function filterFlow(page) {
  await goFast(page, `${BASE}/`, "aside");
  await sleep(1100);
  const skadis = page.getByRole("button", { name: /^skadis\s*\d/ }).first();
  if (await skadis.count()) {
    await skadis.click();
    await sleep(1500);
    await skadis.click(); // clear
    await sleep(600);
  }
  const car = page.getByRole("button", { name: /^car\s*\d/ }).first();
  if (await car.count()) {
    await car.click();
    await sleep(1500);
    await car.click();
    await sleep(600);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
