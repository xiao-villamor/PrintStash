/** Deterministic contracts against the production build; timing samples live separately. */
import { test, expect } from "@playwright/test";
import { aJob } from "../../src/test-support/factories";
const api = `http://127.0.0.1:${process.env.STARTUP_API_PORT ?? 8420}`;

test.describe("production library startup", () => {
  test.skip(
    Boolean(process.env.STARTUP_FRONTEND_DIR),
    "The before snapshot has no startup coordinator",
  );
  test.beforeEach(async ({ page, request }) => {
    const login = await request.post(`${api}/api/v1/auth/login`, {
      data: { username: "admin", password: "admin1234" },
    });
    expect(login.ok()).toBe(true);
    const token: string = (await login.json()).access_token;
    const me = await request.get(`${api}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    await page
      .context()
      .addCookies([
        { name: "printstash_session", value: token, url: api, httpOnly: true, sameSite: "Strict" },
      ]);
    await page.addInitScript(
      (user) => {
        localStorage.setItem("printstash.user", user);
        localStorage.setItem("printstash.locale", "en");
      },
      await me.text(),
    );
  });

  async function ready(page: import("@playwright/test").Page) {
    await page.waitForFunction(
      () => performance.getEntriesByName("printstash:library-ready").length > 0,
    );
  }

  test("serves production asset caching headers", async ({ page, request }) => {
    await page.goto("/");
    await ready(page);
    const asset = await page.locator('script[type="module"]').getAttribute("src");
    expect(asset).toMatch(/^\/assets\//);
    const response = await request.get(asset!);
    expect(response.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
    const worker = await request.get("/sw.js");
    expect(worker.headers()["cache-control"]).toBe("no-store");
    const document = await request.get("/");
    expect(document.headers()["cache-control"]).toBe("no-store");
  });

  test("allows early filter interaction without prematurely loading other secondary reads", async ({
    page,
  }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(new URL(request.url()).pathname));
    let release: () => void = () => {};
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/v1/models/page?**", async (route) => {
      await pending;
      await route.continue();
    });
    try {
      await page.goto("/");
      await expect.poll(() => requests.includes("/api/v1/models/page")).toBe(true);
      expect(requests).not.toContain("/api/v1/models/facets");
      expect(requests).not.toContain("/api/v1/saved-views");
      expect(requests).not.toContain("/api/v1/jobs");
      await page.getByRole("button", { name: "Filters", exact: true }).click();
      await expect.poll(() => requests.includes("/api/v1/models/facets")).toBe(true);
      expect(requests).not.toContain("/api/v1/saved-views");
      release();
      await ready(page);
      await expect.poll(() => requests.includes("/api/v1/saved-views")).toBe(true);
    } finally {
      release();
    }
  });

  test("retrieves both precached bootstrap scripts offline", async ({ page }) => {
    await page.goto("/");
    await ready(page);
    await expect
      .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
      .toBe(true);
    await page.context().setOffline(true);
    try {
      const navigation = await page.reload({ waitUntil: "domcontentloaded" });
      expect(navigation?.ok()).toBe(true);
      const scripts = await page.evaluate(async () =>
        Promise.all(
          ["/theme-bootstrap.js", "/locale-shell.js"].map(async (path) => {
            const response = await fetch(path);
            return { ok: response.ok, bytes: (await response.text()).length };
          }),
        ),
      );
      expect(scripts).toHaveLength(2);
      for (const script of scripts) {
        expect(script.ok).toBe(true);
        expect(script.bytes).toBeGreaterThan(100);
      }
    } finally {
      await page.context().setOffline(false);
    }
  });

  test("opens each deferred form on its first use", async ({ page }) => {
    const scripts: string[] = [];
    page.on("request", (request) => {
      if (request.resourceType() === "script") scripts.push(new URL(request.url()).pathname);
    });
    await page.goto("/?c=collection-01");
    await ready(page);
    expect(
      scripts.some((path) =>
        /upload-modal|new-multipart-model-modal|model-tags-dialog|archive-review/.test(path),
      ),
    ).toBe(false);
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const upload = page.getByRole("dialog", { name: "Upload model" });
    await expect(upload.locator('input[type="file"]').first()).toBeAttached();
    await upload.getByRole("button", { name: "Close", exact: true }).click();
    await expect(upload).toHaveCount(0);
    await page.getByRole("button", { name: "New multipart set", exact: true }).click();
    const multipart = page.getByRole("dialog", { name: "New multipart set" });
    await expect(multipart.getByRole("textbox").first()).toBeEditable();
    await multipart.getByRole("button", { name: "Close", exact: true }).click();
    await expect(multipart).toHaveCount(0);
    await page
      .getByRole("button", { name: /(?:Add tags to|Edit tags for) Benchmark model/ })
      .first()
      .click();
    const tags = page.getByRole("dialog");
    await expect(tags.getByLabel("Search or create a tag")).toBeEditable();
    await tags.getByRole("button", { name: "Close", exact: true }).click();
    await expect(tags).toHaveCount(0);
    // Only the ZIP manifest is stood in for here; the purpose is the lazy form boundary.
    await page.route("**/api/v1/jobs/startup-review", (route) =>
      route.fulfill({
        json: aJob({
          job_id: "startup-review",
          kind: "ingestion.archive_inspect",
          state: "completed",
          result: {
            kind: "archive_manifest",
            archive_id: "startup-archive",
            archive_name: "parts.zip",
            entries: [{ name: "part.stl", size_bytes: 100, file_type: "stl", is_image: false }],
          },
        }),
      }),
    );
    await page.evaluate(() =>
      window.dispatchEvent(
        new CustomEvent("printstash:review-archive", { detail: "startup-review" }),
      ),
    );
    await expect(page.getByRole("dialog").getByText("part.stl", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("dialog").getByRole("button", { name: /select all/i }),
    ).toBeEnabled();
  });

  for (const mode of ["organized", "all", "multipart", "components"] as const) {
    test(`preserves URL filters in the first ${mode} result`, async ({ page }) => {
      const modelRequests: URL[] = [];
      page.on("request", (request) => {
        const url = new URL(request.url());
        if (url.pathname === "/api/v1/models/page") modelRequests.push(url);
      });
      await page.addInitScript((mode) => {
        localStorage.setItem("ps-vault-library-view", mode);
      }, mode);
      await page.goto("/?c=collection-01&tag=benchmark");
      await ready(page);
      expect(modelRequests.length).toBeGreaterThan(0);
      expect(modelRequests[0].searchParams.get("collection")).toBe("collection-01");
      expect(modelRequests[0].searchParams.getAll("tag")).toEqual(["benchmark"]);
      const names = await page.locator("main article h2").allTextContents();
      if (mode === "multipart" || mode === "components") expect(names).toEqual([]);
      else {
        expect(names.length).toBeGreaterThan(0);
        for (const name of names) {
          const index = Number(name.match(/(\d+)$/)?.[1]);
          expect(index % 2).toBe(1);
        }
      }
    });
  }

  if (process.env.STARTUP_DISTRIBUTION === "dense") {
    test("pages through all ninety models after the bounded first render", async ({ page }) => {
      await page.goto("/?c=collection-01");
      await ready(page);
      await expect(page.locator("main article")).toHaveCount(24);
      while (await page.getByRole("button", { name: /Load more/ }).count()) {
        const count = await page.locator("main article").count();
        await page.getByRole("button", { name: /Load more/ }).click();
        await expect.poll(() => page.locator("main article").count()).toBeGreaterThan(count);
      }
      await expect(page.locator("main article")).toHaveCount(90);
    });
  }
});
