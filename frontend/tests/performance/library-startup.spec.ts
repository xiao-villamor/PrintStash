/** Production assets + real API + exact SQLite corpus. Timings are local observations. */
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

// Tracing/snapshots add work to the startup being measured. Functional specs retain traces.
test.use({ trace: "off" });

const distribution = process.env.STARTUP_DISTRIBUTION ?? "distributed";
const api = `http://127.0.0.1:${process.env.STARTUP_API_PORT ?? 8420}`;
const base = `http://127.0.0.1:${process.env.STARTUP_PORT ?? 3420}`;
const destination = distribution === "dense" ? "/?c=collection-01" : "/";
const warmCount = Number(process.env.STARTUP_WARM_RUNS ?? 30);
const coldCount = Number(process.env.STARTUP_COLD_RUNS ?? 20);
const baseline = Boolean(process.env.STARTUP_FRONTEND_DIR);
if (![warmCount, coldCount].every((count) => Number.isSafeInteger(count) && count > 0)) {
  throw new Error("startup sample counts must be positive integers");
}

async function prepared(browser: Browser, locale: "en" | "es", token: string, user: string) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale });
  await context.addCookies([
    { name: "printstash_session", value: token, url: base, httpOnly: true, sameSite: "Strict" },
  ]);
  await context.addInitScript(
    ({ user, locale, distribution }) => {
      localStorage.setItem("printstash.user", user);
      localStorage.setItem("printstash.locale", locale);
      const mark = (name: string) => {
        if (!performance.getEntriesByName(name).length) performance.mark(name);
      };
      let scheduled = false;
      const visible = (element: Element | null) => {
        const bounds = element?.getBoundingClientRect();
        return bounds !== undefined && bounds.width > 0 && bounds.height > 0;
      };
      const observer = new MutationObserver(() => {
        const card = document.querySelector(
          distribution === "dense"
            ? 'main article a[href^="/models/"]'
            : "main [data-collection-path]",
        );
        const tree = [...document.querySelectorAll("aside button")].find((button) =>
          button.textContent?.includes("Collection 01"),
        );
        if (visible(card)) mark("observed:cards");
        if (visible(tree ?? null)) mark("observed:tree");
        if (visible(card) && visible(tree ?? null) && !scheduled) {
          scheduled = true;
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              mark("observed:ready");
              observer.disconnect();
            }),
          );
        }
      });
      observer.observe(document, { subtree: true, childList: true, attributes: true });
    },
    { user, locale, distribution },
  );
  const page = await context.newPage();
  // Install the production worker before the first app visit without loading app JS.
  await page.goto(`${base}/offline.html`);
  await page.evaluate(async () => {
    await navigator.serviceWorker.register("/sw.js");
    await navigator.serviceWorker.ready;
  });
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);
  return { context, page };
}

async function sample(page: Page) {
  await page.mouse.move(0, 0);
  await page.goto(`${base}${destination}`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => performance.getEntriesByName("observed:ready").length > 0);
  await expect(
    page.locator("aside").getByRole("button", { name: "Collection 01", exact: true }),
  ).toBeVisible();
  if (!baseline) {
    await page.waitForFunction(
      () => performance.getEntriesByName("printstash:library-ready").length > 0,
    );
    const painted = await page.evaluate(() => ({
      ready: performance.getEntriesByName("printstash:library-ready")[0].startTime,
      cards: performance.getEntriesByName("observed:cards")[0].startTime,
      tree: performance.getEntriesByName("observed:tree")[0].startTime,
    }));
    // Scheduling can delay our two post-paint marks by different frame counts.
    // Readiness must never precede visible cards/tree; its drift is recorded.
    expect(painted.ready).toBeGreaterThanOrEqual(Math.max(painted.cards, painted.tree));
  }
  await page.waitForFunction(() => {
    const containers = [...document.querySelectorAll('main article a[href^="/models/"]')].filter(
      (card) => {
        const bounds = card.getBoundingClientRect();
        return bounds.width > 0 && bounds.top < innerHeight && bounds.bottom > 0;
      },
    );
    return containers.every((card) => {
      const image = card.querySelector("img");
      return image?.complete && image.naturalWidth > 0;
    });
  });
  const record = await page.evaluate(() => ({
    ready: performance.getEntriesByName("observed:ready")[0].startTime,
    marks: Object.fromEntries(
      performance.getEntriesByType("mark").map((entry) => [entry.name, entry.startTime]),
    ),
    thumbnailsObserved: performance.now(),
    visibleImages: [...document.querySelectorAll("main img")].filter(
      (image) => image.getBoundingClientRect().top < innerHeight,
    ).length,
    serviceWorker: Boolean(navigator.serviceWorker.controller),
    resources: performance.getEntriesByType("resource").map((entry) => {
      // SAFETY: resource entries are returned by the browser with PerformanceResourceTiming fields.
      const resource = entry as PerformanceResourceTiming;
      return {
        path: new URL(resource.name).pathname,
        start: resource.startTime,
        duration: resource.duration,
        decoded: resource.decodedBodySize,
        transfer: resource.transferSize,
        server: resource.serverTiming.map((timing) => ({
          name: timing.name,
          duration: timing.duration,
          description: timing.description,
        })),
      };
    }),
  }));
  expect(record.serviceWorker).toBe(true);
  // A real navigation proves the rendered tree handles input, beyond marks/skeletons.
  await page.locator("aside").getByRole("button", { name: "Collection 02", exact: true }).click();
  await expect(page).toHaveURL(/c=collection-02/);
  await page.waitForLoadState("networkidle");
  return record;
}

function statistics(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  const percentile = (quantile: number) =>
    sorted[Math.max(0, Math.ceil(sorted.length * quantile) - 1)];
  const middle = Math.floor(sorted.length / 2);
  const median = (sorted[Math.floor((sorted.length - 1) / 2)] + sorted[middle]) / 2;
  return { median, p95: percentile(0.95), runs: values.length };
}

test.describe("library startup budget", () => {
  for (const locale of ["en", "es"] as const) {
    test(`${distribution} startup (${locale}) with active service worker`, async ({
      browser,
      request,
    }, testInfo) => {
      const login = await request.post(`${api}/api/v1/auth/login`, {
        data: { username: "admin", password: "admin1234" },
      });
      expect(login.ok()).toBe(true);
      const token: string = (await login.json()).access_token;
      const user = await (
        await request.get(`${api}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
        })
      ).text();
      const warm: Awaited<ReturnType<typeof sample>>[] = [];
      const cold: Awaited<ReturnType<typeof sample>>[] = [];
      let context: BrowserContext | undefined;
      try {
        const initial = await prepared(browser, locale, token, user);
        context = initial.context;
        await sample(initial.page);
        for (let index = 0; index < warmCount; index++) {
          warm.push(await sample(initial.page));
        }
      } finally {
        await context?.close();
      }
      for (let index = 0; index < coldCount; index++) {
        const fresh = await prepared(browser, locale, token, user);
        try {
          cold.push(await sample(fresh.page));
        } finally {
          await fresh.context.close();
        }
      }
      const warmStats = statistics(warm.map((record) => record.ready));
      const coldStats = statistics(cold.map((record) => record.ready));
      const result = {
        distribution,
        locale,
        variant: baseline ? "before" : "after",
        revision: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
        dirty:
          execFileSync("git", ["status", "--porcelain"], { encoding: "utf8" }).trim().length > 0,
        version: JSON.parse(
          await readFile(resolve(process.env.STARTUP_FRONTEND_DIR ?? ".", "package.json"), "utf8"),
        ).version,
        browser: browser.version(),
        node: process.version,
        delivery: "production-nginx",
        nginxImage: execFileSync(
          "docker",
          ["image", "inspect", "--format", "{{.Id}}", "nginxinc/nginx-unprivileged:alpine"],
          { encoding: "utf8" },
        ).trim(),
        warm: warmStats,
        cold: coldStats,
        samples: { warm, cold },
      };
      const directory = resolve(process.env.STARTUP_REPORT_DIR ?? ".startup-results/metrics");
      await mkdir(directory, { recursive: true });
      const path = resolve(directory, `${result.variant}-${distribution}-${locale}.json`);
      await writeFile(path, JSON.stringify(result, null, 2));
      await testInfo.attach("startup measurements", { path, contentType: "application/json" });
      console.log(
        JSON.stringify({
          distribution,
          locale,
          variant: result.variant,
          warm: warmStats,
          cold: coldStats,
        }),
      );
      if (process.env.STARTUP_ENFORCE_BUDGET === "1") {
        expect.soft(warmStats.median).toBeLessThanOrEqual(500);
        expect.soft(warmStats.p95).toBeLessThanOrEqual(800);
        expect.soft(coldStats.median).toBeLessThanOrEqual(1_000);
      }
    });
  }
});
