/** The shared safety net distinguishes app cancellation from broken delivery. */
import { expect, test } from "@playwright/test";
import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

test.describe("page problem collection", () => {
  test("accepts an explicit AbortSignal cancellation of a pending fetch", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.route("**/collector-pending", () => {});
    await page.route("**/collector-ready", (route) =>
      route.fulfill({ status: 200, body: "ready" }),
    );
    await page.goto("/login");
    const failure = page.waitForEvent("requestfailed", {
      predicate: (request) => request.url().endsWith("/collector-pending"),
    });

    const name = await page.evaluate(async () => {
      const controller = new AbortController();
      const pending = fetch("/collector-pending", { signal: controller.signal }).then(
        () => "unexpected success",
        (error: Error) => error.name,
      );
      await fetch("/collector-ready");
      controller.abort();
      return pending;
    });
    await failure;

    expect(name).toBe("AbortError");
    await expect.poll(() => problems).toEqual([]);
  });

  test("reports a browser abort without an app cancellation", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.route("**/collector-unexpected", (route) => route.abort("aborted"));
    await page.goto("/login");
    const failure = page.waitForEvent("requestfailed", {
      predicate: (request) => request.url().endsWith("/collector-unexpected"),
    });

    await page.evaluate(() => fetch("/collector-unexpected").catch(() => null));
    const request = await failure;

    expect(request.failure()?.errorText).toBe("net::ERR_ABORTED");
    expect(problems).toContain(`request failed: ${request.url()} net::ERR_ABORTED`);
  });

  test("preserves server errors in the collector", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.route("**/collector-server-error", (route) =>
      route.fulfill({ status: 500, body: "failed" }),
    );
    await page.goto("/login");
    await page.evaluate(() => fetch("/collector-server-error"));

    await expect
      .poll(() =>
        problems.some(
          (problem) =>
            problem.startsWith("bad response: 500 ") && problem.endsWith("/collector-server-error"),
        ),
      )
      .toBe(true);
  });
});
