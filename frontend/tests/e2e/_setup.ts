/*
 * The mock-API lifecycle and page seeding every spec in this folder needs.
 *
 * These specs share one mock server and one authenticated user, and both are
 * easy to get subtly wrong per-file. The server is started once per worker and
 * its state is reset before each test — without the reset, a spec that creates
 * something leaks it into the next one and the failure lands in a file that did
 * nothing wrong. And every non-public route redirects to /login, so a token and
 * user have to be in `localStorage` *before* the first navigation: seeding after
 * `goto` renders the login screen and the assertion fails on a page the test
 * never meant to visit.
 *
 * `collectPageProblems` is the shared "nothing went wrong" net. Most of what
 * this tier catches is not a wrong assertion but a console error, a 404 on a
 * route that used to resolve, or a failed request — none of which fail a test on
 * their own. It is a subscription rather than a snapshot, so it must be
 * installed before the navigation it is watching.
 */
import { expect, test, type Page } from "@playwright/test";
import type { Server } from "node:http";

import { resetMockApiState, startMockApi } from "./mock-api";
import { RequestFailures } from "./_request-failures";

declare global {
  interface Window {
    __printstashExplicitFetchAbort: (method: string, url: string) => Promise<void>;
  }
}

export const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 4210);

/** Start the mock API for the file, reset its state per test, seed an auth'd user. */
export function useMockApi(): void {
  let api: Server;

  test.beforeAll(async () => {
    api = await startMockApi(apiPort);
  });

  test.afterAll(async () => {
    await new Promise<void>((resolve, reject) => {
      api.close((error) => (error ? reject(error) : resolve()));
    });
  });

  test.beforeEach(async ({ page }) => {
    resetMockApiState();
    await page.addInitScript(() => {
      localStorage.setItem("printstash.token", "test-token");
      localStorage.setItem(
        "printstash.user",
        JSON.stringify({
          id: 1,
          username: "tester",
          email: null,
          is_superuser: true,
        }),
      );
    });
  });
}

/**
 * Subscribe to everything that indicates the page broke without failing a test.
 *
 * Install before the `goto` it should cover; the returned array fills as events
 * arrive, so asserting it empty at the end of a test is the check.
 */
export async function collectPageProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  const requests = new RequestFailures(problems);
  await page.exposeBinding(
    "__printstashExplicitFetchAbort",
    (_source, method: string, url: string) => requests.cancel(method, url),
  );
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.fetch = (input, init) => {
      const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
      if (!signal || signal.aborted) return originalFetch(input, init);
      const method = (
        init?.method ?? (input instanceof Request ? input.method : "GET")
      ).toUpperCase();
      const url = new URL(input instanceof Request ? input.url : String(input), location.href).href;
      // A canceled signal alone is insufficient: completed fetches are also
      // canceled during cleanup. Require this fetch to reject with AbortError.
      return originalFetch(input, init).catch(async (error: Error) => {
        if (signal.aborted && error instanceof DOMException && error.name === "AbortError")
          await window.__printstashExplicitFetchAbort(method, url).catch(() => {});
        throw error;
      });
    };
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      if (message.text().includes("/api/v1/printers/3/ws")) return;
      problems.push(`console error: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    problems.push(`page error: ${error.message}`);
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (url.includes("_rsc=")) return;
    requests.failed(request.method(), url, request.failure()?.errorText ?? "");
  });
  page.on("response", (response) => {
    const url = response.url();
    if (response.status() >= 400 && !url.includes("_rsc=")) {
      problems.push(`bad response: ${response.status()} ${url}`);
    }
  });
  return problems;
}

/** Computed animation-delay of every direct child of the staggered model grid. */
export async function gridDelays(page: Page): Promise<string[]> {
  await page.goto("/");
  const grid = page.locator(".stagger-children").first();
  await expect(grid.locator("> *").first()).toBeAttached();
  return grid.evaluate((el) =>
    Array.from(el.children).map((c) => getComputedStyle(c).animationDelay),
  );
}
