/** Separate instrumentation records resource cost; it never substitutes for startup timings. */
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

declare global {
  interface Window {
    __startupResources: { commits: number; blobBytes: number; blobUrls: number };
  }
}
const api = `http://127.0.0.1:${process.env.STARTUP_API_PORT ?? 8420}`;
test.use({ trace: "off" });

test.describe("instrumented startup resources", () => {
  test("records resources for a usable dense library", async ({ page, request }, testInfo) => {
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
    await page
      .context()
      .addCookies([
        { name: "printstash_session", value: token, url: api, httpOnly: true, sameSite: "Strict" },
      ]);
    await page.addInitScript((user) => {
      localStorage.setItem("printstash.user", user);
      localStorage.setItem("printstash.locale", "en");
      const state = { commits: 0, blobBytes: 0, blobUrls: 0 };
      window.__startupResources = state;
      Reflect.set(window, "__REACT_DEVTOOLS_GLOBAL_HOOK__", {
        supportsFiber: true,
        inject: () => 1,
        onCommitFiberRoot: () => {
          state.commits += 1;
        },
        onCommitFiberUnmount: () => {},
      });
      const retained = new Map<string, number>();
      const create = URL.createObjectURL.bind(URL);
      const revoke = URL.revokeObjectURL.bind(URL);
      const update = () => {
        state.blobBytes = [...retained.values()].reduce((sum, size) => sum + size, 0);
        state.blobUrls = retained.size;
      };
      URL.createObjectURL = (value) => {
        const url = create(value);
        retained.set(url, value instanceof Blob ? value.size : 0);
        update();
        return url;
      };
      URL.revokeObjectURL = (url) => {
        retained.delete(url);
        update();
        revoke(url);
      };
    }, user);
    await page.goto("/offline.html");
    await page.evaluate(async () => {
      await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
    });
    await expect
      .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
      .toBe(true);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Performance.enable");
    async function snapshot() {
      const cpu = await cdp.send("Performance.getMetrics");
      const browser = await page.evaluate(() => ({
        ...window.__startupResources,
        timeOrigin: performance.timeOrigin,
        resources: performance.getEntriesByType("resource").flatMap((entry) =>
          entry instanceof PerformanceResourceTiming
            ? [
                {
                  path: new URL(entry.name).pathname,
                  duration: entry.duration,
                  beforeRequest:
                    entry.requestStart > 0 ? entry.requestStart - entry.fetchStart : null,
                  responseWait:
                    entry.requestStart > 0 ? entry.responseStart - entry.requestStart : null,
                  transfer: entry.transferSize,
                  decoded: entry.decodedBodySize,
                },
              ]
            : [],
        ),
      }));
      return {
        cpu: Object.fromEntries(cpu.metrics.map(({ name, value }) => [name, value])),
        ...browser,
      };
    }
    await page.goto("/?c=collection-01");
    await expect(page.locator("main article")).toHaveCount(24);
    await page.waitForFunction(() => {
      const images = [...document.querySelectorAll("main article img")].filter((image) => {
        const bounds = image.getBoundingClientRect();
        return bounds.top < innerHeight && bounds.bottom > 0 && bounds.width > 0;
      });
      return (
        images.length > 0 &&
        images.every(
          (image) => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0,
        )
      );
    });
    await page.locator("main article img").evaluateAll(async (images) => {
      await Promise.all(
        images
          .filter(
            (image) =>
              image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0,
          )
          .map((image) => (image instanceof HTMLImageElement ? image.decode() : undefined)),
      );
    });
    const first = await snapshot();
    expect(first.commits).toBeGreaterThan(0);
    expect(first.cpu.ScriptDuration).toBeGreaterThan(0);
    expect(first.blobBytes).toBeGreaterThan(0);
    while (await page.getByRole("button", { name: /Load more/ }).count()) {
      const count = await page.locator("main article").count();
      await page.getByRole("button", { name: /Load more/ }).click();
      await expect.poll(() => page.locator("main article").count()).toBeGreaterThan(count);
    }
    await expect(page.locator("main article")).toHaveCount(90);
    await page.waitForLoadState("networkidle");
    const expanded = await snapshot();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect.poll(() => page.evaluate(() => window.__startupResources.blobBytes)).toBe(0);
    const retired = await snapshot();
    expect(retired.timeOrigin).toBe(expanded.timeOrigin);
    expect(retired.blobUrls).toBe(0);
    const directory = resolve(process.env.STARTUP_REPORT_DIR ?? ".startup-results/metrics");
    await mkdir(directory, { recursive: true });
    const path = resolve(directory, "instrumented-resources.json");
    await writeFile(
      path,
      JSON.stringify(
        {
          revision: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
          instrumentation: "CDP metrics, React root commit observer, encoded Blob URL lifecycle",
          first,
          expanded,
          retired,
        },
        null,
        2,
      ),
    );
    await testInfo.attach("instrumented resources", { path, contentType: "application/json" });
    await cdp.detach();
  });
});
