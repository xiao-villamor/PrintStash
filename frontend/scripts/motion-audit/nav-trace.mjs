#!/usr/bin/env node
/**
 * Library navigation trace: what each folder navigation costs.
 *
 *   PRINTSTASH_USER=… PRINTSTASH_PASSWORD=… node scripts/motion-audit/nav-trace.mjs \
 *     --base http://localhost:3474 --path "/?c=<folder>&cid=<id>" --child <child-slug>
 *
 * Steps: open a child folder from its card, return through the tree, open it
 * again (warm), browser back, All Models. For each step it reports the API
 * requests by endpoint, exact duplicates, whether the document reloaded, the
 * time until the destination's cards are on screen, and long animation frames.
 * The HTTP cache is disabled (a cold visit), like the motion audit.
 */
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";

const { values: args } = parseArgs({
  options: {
    base: { type: "string", default: "http://localhost:3474" },
    path: {
      type: "string",
      default: "/?type=all&sort=date-desc&c=printstash-data%2Fprintstash-data%2Forganization&cid=8",
    },
    child: { type: "string", default: "gridfinity" },
    parent: { type: "string", default: "Organization" },
  },
});

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  colorScheme: "dark",
});
const page = await context.newPage();
const start = args.base + args.path;
await page.goto(start);
const login = page.getByRole("textbox", { name: "Username" });
await Promise.race([
  login.waitFor({ timeout: 30000 }),
  page.waitForSelector("main [data-library-key^='/models']", { timeout: 30000 }),
]);
if (await login.isVisible()) {
  await login.fill(process.env.PRINTSTASH_USER ?? "");
  await page.getByRole("textbox", { name: "Password" }).fill(process.env.PRINTSTASH_PASSWORD ?? "");
  await page.evaluate(() => document.querySelector("form")?.requestSubmit());
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30000 });
  await page.goto(start);
}
await page.waitForSelector("main [data-library-key^='/models']", { timeout: 30000 });
const cdp = await context.newCDPSession(page);
await cdp.send("Network.enable");
await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
const probe = () => {
  window.__nav = { long: [] };
  new PerformanceObserver((list) => {
    for (const e of list.getEntries())
      if (e.duration > 50) window.__nav.long.push(Math.round(e.duration));
  }).observe({ type: "long-animation-frame" });
};
await page.addInitScript(probe);
await page.evaluate(probe);
await page.waitForTimeout(2500);

let documents = 0;
page.on("load", () => (documents += 1));
let log = [];
page.on("request", (r) => {
  if (r.url().includes("/api/")) log.push(r.url().replace(/^https?:\/\/[^/]+/, ""));
});

const childCard = () =>
  page
    .locator(
      `main [data-library-key^='collection:'] [data-collection-path$='/${args.child}'] button`,
    )
    .first();

async function step(name, title, action) {
  log = [];
  const before = documents;
  const longBefore = await page.evaluate(() => window.__nav?.long.length ?? 0);
  const started = Date.now();
  await action();
  await page
    .waitForFunction(
      (title) =>
        document.querySelector("main h1")?.textContent?.trim().toLowerCase() ===
          title.toLowerCase() &&
        [...document.querySelectorAll("main [data-library-key^='/models']")].some(
          (c) =>
            !c.closest(".library-loading-placeholder") &&
            !c.querySelector(".library-card-fill") &&
            c.getBoundingClientRect().top < innerHeight,
        ),
      title,
      { timeout: 15000 },
    )
    .catch(() => null);
  const visibleMs = Date.now() - started;
  await page.waitForTimeout(1500);
  const reloaded = documents > before;
  const long = await page.evaluate(
    (n) => (window.__nav?.long ?? []).slice(n),
    reloaded ? 0 : longBefore,
  );
  const byEndpoint = new Map();
  const exact = new Map();
  for (const url of log) {
    const kind = url.split("?")[0].replace(/\/\d+/g, "/:id");
    byEndpoint.set(kind, (byEndpoint.get(kind) ?? 0) + 1);
    exact.set(url, (exact.get(url) ?? 0) + 1);
  }
  return {
    name,
    reached: (await page.locator("main h1").first().textContent())?.trim(),
    reloaded,
    visibleMs,
    requests: log.length,
    byEndpoint: [...byEndpoint].sort((a, b) => b[1] - a[1]).map(([k, v]) => `${v}× ${k}`),
    duplicates: [...exact].filter(([, n]) => n > 1).map(([u, n]) => `${n}× ${u.slice(0, 120)}`),
    longFrames: long,
  };
}

const results = [
  await step(`open ${args.child} (card)`, args.child.replace(/-/g, " "), () => childCard().click()),
  await step(`tree → ${args.parent}`, args.parent, () =>
    page.locator("#collections-pane").getByText(args.parent, { exact: true }).first().click(),
  ),
  await step(`open ${args.child} again (warm)`, args.child.replace(/-/g, " "), () =>
    childCard().click(),
  ),
  await step("browser back", args.parent, () => page.goBack()),
  await step("All Models (breadcrumb)", "All Models", () =>
    page
      .locator(".shell-topbar")
      .getByRole("button", { name: "All Models", exact: true })
      .first()
      .click(),
  ),
];
await browser.close();
for (const r of results) {
  console.log(`\n## ${r.name} → ${r.reached}${r.reloaded ? " (FULL RELOAD)" : ""}`);
  console.log(
    `visible ${r.visibleMs} ms · ${r.requests} requests · long frames ${JSON.stringify(r.longFrames)}`,
  );
  console.log(`  ${r.byEndpoint.join(" · ")}`);
  if (r.duplicates.length) console.log(`  duplicates: ${r.duplicates.join(" | ")}`);
}
