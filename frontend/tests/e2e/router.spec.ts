/** SPA navigation keeps public admission, pending private authority and existing search URLs distinct. */
import { expect, test } from "@playwright/test";
import { aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Router boundaries", () => {
  test("opens a public share route without probing the private session", async ({ page }) => {
    const privateReads: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/v1\/(auth\/me|setup\/status)/.test(request.url()))
        privateReads.push(request.url());
    });
    await page.route("**/api/v1/share/router-public", (route) =>
      route.fulfill({ status: 404, json: { detail: "not_found" } }),
    );

    await page.goto("/share/router-public");
    await expect(page.getByText("This share link is invalid, expired, or revoked.")).toBeVisible();
    await page.reload();

    await expect(page.getByText("This share link is invalid, expired, or revoked.")).toBeVisible();
    expect(privateReads).toEqual([]);
    await expect(page.getByRole("navigation", { name: "Navigation" })).toHaveCount(0);
  });

  test("waits for verified session authority before a private Library read", async ({ page }) => {
    const verified = Promise.withResolvers<void>();
    const sessions: string[] = [];
    const reads: string[] = [];
    const model = aModelListItem({ id: 42, name: "Verified Router model" });
    await page.route("**/api/v1/auth/me", async (route) => {
      sessions.push(route.request().url());
      await verified.promise;
      await route.fulfill({
        json: {
          id: 1,
          username: "tester",
          email: null,
          is_superuser: true,
          is_active: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      });
    });
    await page.route("**/api/v1/models/browse?**", (route) => {
      reads.push(route.request().url());
      return route.fulfill({
        json: {
          items: [{ kind: "model", model }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?tag=Desk&tag=Tools&type=all&sort=name-asc");
    await expect.poll(() => sessions.length).toBeGreaterThan(0);

    expect(reads).toEqual([]);
    verified.resolve();

    await expect(
      page
        .getByRole("main")
        .getByRole("link", { name: /Verified Router model/ })
        .first(),
    ).toBeVisible();
    expect(new URL(page.url()).searchParams.getAll("tag")).toEqual(["Desk", "Tools"]);
    await page.reload();
    await expect(
      page
        .getByRole("main")
        .getByRole("link", { name: /Verified Router model/ })
        .first(),
    ).toBeVisible();
    expect(new URL(page.url()).searchParams.getAll("tag")).toEqual(["Desk", "Tools"]);
  });

  test("keeps a lazy destination after outgoing Library canonicalization", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const destination = page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("link", { name: "Profiles", exact: true });
    await expect(destination).toBeVisible();

    await destination.click();

    await expect(page).toHaveURL(/\/profiles$/);
    await expect(destination).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { name: "Profiles", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Profiles", exact: true })).toBeVisible();
  });
});
