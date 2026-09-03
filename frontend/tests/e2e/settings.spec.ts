/*
 * The settings route: deep links, and the three things it hands out.
 *
 * Sections are deep-linkable and preserve navigation state, which matters because
 * every support answer about PrintStash is a link to a settings section — one that
 * lands on the overview instead is an answer that does not work.
 *
 * The extension setup package and the pairing code are both credentials with a
 * lifetime. The package is one-time and the code is temporary; a UI that
 * re-displays either turns a short-lived secret into a long-lived one sitting on
 * a screen.
 *
 * Preview settings queue image recreation rather than applying silently, since
 * changing quality invalidates every thumbnail in the library. And the release
 * warning is the only thing telling a self-hoster they are running something old.
 * Backup source identities are deliberately opaque and may be wider than prose;
 * the restore warning must keep them inside its confirmation dialog.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";

useMockApi();

test.describe("settings route", () => {
  test("settings sections are deep-linkable and preserve navigation state", async ({ page }) => {
    await page.goto("/settings?section=trash");
    await expect(page.getByRole("heading", { name: "Trash retention" })).toBeVisible();
    await expect(page.getByLabel("Trash size")).toHaveText("1.5 MB reclaimable");
    await expect(page.getByText("1 deleted model")).toBeVisible();
    await page.getByRole("button", { name: "About" }).click();
    await expect(page).toHaveURL(/\/settings\?section=about$/);
    await expect(page.getByRole("heading", { name: "Latest changes" })).toBeVisible();
  });

  test("discovers an exact legacy S3 source for adoption", async ({ page }) => {
    await page.goto("/settings?section=backup");

    await expect(page.getByText("nexus3d-backups/legacy-2025.tar.gz")).toBeVisible();
    await expect(page.getByText(/SHA-256 a{16}/)).toBeVisible();

    await page.getByRole("button", { name: "Adopt backup" }).click();
    const dialog = page.getByRole("dialog", { name: "Adopt legacy cloud backup?" });
    await expect(dialog).toContainText("nexus3d-backups/legacy-2025.tar.gz");
    await expect(dialog).toContainText("a".repeat(16));

    const request = page.waitForRequest((candidate) => {
      const url = new URL(candidate.url());
      return (
        candidate.method() === "POST" &&
        url.pathname === "/api/v1/backups/adopt-s3" &&
        url.searchParams.get("key") === "nexus3d-backups/legacy-2025.tar.gz" &&
        url.searchParams.get("source_ref") === "s3-legacy-source" &&
        url.searchParams.get("expected_archive_sha256") === "a".repeat(64)
      );
    });
    await dialog.getByRole("button", { name: "Adopt backup" }).click();
    await request;
    await expect(page.getByRole("button", { name: "Adopt backup" })).toHaveCount(0);
    await expect(page.getByText("Provider: provider-legacy-…")).toBeVisible();
    await expect(page.getByText("Exact key: nexus3d-backups/legacy-2025.tar.gz")).toBeVisible();
  });

  test("contains long backup metadata within the restore dialog", async ({ page }) => {
    await page.route("**/api/v1/backups/sources", async (route) => {
      await route.fulfill({
        json: [
          {
            backup_id: "2026-01-01T000000Z",
            created_at: "2026-01-01T00:00:00Z",
            size_bytes: 4096,
            file_count: 12,
            storage_backend: "local",
            app_version: "0.13.0",
            location: "local",
            source_ref: "9".repeat(64),
            namespace: "backup/data/backups",
            archive_sha256: "a".repeat(64),
            canonical: true,
            precedence: 1,
          },
        ],
      });
    });
    await page.goto("/settings?section=backup");
    await page.getByRole("button", { name: "Restore", exact: true }).click();

    const dialog = page.getByRole("dialog", { name: "Restore backup?" });
    const description = dialog.getByText(/^This replaces the current database/);
    await expect(description).toBeVisible();

    const overflow = await dialog.evaluate((element) => element.scrollWidth - element.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);

    await page.setViewportSize({ width: 360, height: 740 });
    const narrowOverflow = await dialog.evaluate(
      (element) => element.scrollWidth - element.clientWidth,
    );
    expect(narrowOverflow).toBeLessThanOrEqual(0);
  });

  test("expired trash requires a durable preview before approval", async ({ page }) => {
    await page.goto("/settings?section=trash");
    await page.getByRole("button", { name: "Review expired" }).click();

    const dialog = page.getByRole("dialog", { name: "Create a safe GC preview?" });
    await expect(dialog).toContainText(
      "This only records a bounded candidate plan. It does not delete catalog rows or storage bytes.",
    );
    const previewRequest = page.waitForRequest((candidate) => {
      const url = new URL(candidate.url());
      return candidate.method() === "POST" && url.pathname === "/api/v1/admin/gc";
    });
    await dialog.getByRole("button", { name: "Create preview" }).click();
    await previewRequest;

    await expect(page.getByText("GC plan #7 · preview")).toBeVisible();
    await expect(page.getByText("no backup bound")).toBeVisible();
    await expect(page.getByText("Nothing was deleted.")).toBeVisible();
    const approve = page.getByRole("button", { name: "Verify backup and quarantine" });
    await expect(approve).toBeDisabled();
    await page.getByLabel("Confirm GC plan digest").fill("a".repeat(64));

    const approvalRequest = page.waitForRequest((candidate) => {
      const url = new URL(candidate.url());
      return candidate.method() === "POST" && url.pathname === "/api/v1/admin/gc/7/approve";
    });
    await approve.click();
    await approvalRequest;
    await expect(page.getByText("GC plan #7 · quarantined")).toBeVisible();
    await expect(page.getByText("backup verified", { exact: true })).toBeVisible();
  });

  test("settings prepares a one-time browser extension setup", async ({ page }) => {
    await page.goto("/settings?section=access");

    await page.getByRole("button", { name: "Set up extension" }).click();

    const apiKeys = page.getByRole("group", { name: "API keys" });
    await expect(apiKeys.getByRole("status")).toHaveText("Setup prepared");
    await expect(page.getByRole("button", { name: "Set up extension" })).toHaveCount(0);
    await expect(
      page.getByText(
        "Open the PrintStash extension on this tab to finish the verified connection.",
      ),
    ).toBeVisible();
    const setup = await page.evaluate(() =>
      sessionStorage.getItem("printstash.browser-extension-setup:v1"),
    );
    expect(setup).not.toBeNull();
    expect(setup).toContain('"version":1');
    expect(setup).toContain(`"vault":"${new URL(page.url()).origin}"`);
    expect(setup).toContain('"username":"tester"');
    expect(setup).toContain('"apiKey":"psk_browser_setup_secret"');
  });

  test("settings creates a temporary browser pairing code", async ({ page }) => {
    await page.goto("/settings?section=imports");

    await expect(page.getByRole("heading", { name: "Provider connections" })).toBeVisible();
    await page.getByRole("button", { name: "Create pairing code" }).click();
    await expect(page.getByText("PAIR-1234")).toBeVisible();
    await expect(page.getByText(/Expires at/)).toBeVisible();
  });

  test("preview settings persist quality choices and queue image recreation", async ({ page }) => {
    await page.goto("/settings?section=previews");

    await page.getByLabel("Preview quality").selectOption("detail");
    await page.getByLabel("Screenshot resolution").selectOption("3");
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("printstash.preview.preferences:v1")))
      .toContain('"previewQuality":"detail"');

    await Promise.all([
      page.waitForRequest(
        (request) => request.url().includes("/api/v1/config") && request.method() === "PUT",
      ),
      page.getByLabel("Model image quality").selectOption("1280"),
    ]);

    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/api/v1/files/thumbnails/rebuild?force=true") &&
          response.request().method() === "POST",
      ),
      page.getByRole("button", { name: "Recreate all images" }).click(),
    ]);
    await expect(page.getByText("Model preview recreation queued.")).toBeVisible();
  });

  test("settings warns administrators when a newer release is available", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("PrintStash v0.10.1 is available")).toBeVisible();
    await expect(page.getByRole("link", { name: "View release" })).toHaveAttribute(
      "href",
      "https://github.com/xiao-villamor/PrintStash/releases/tag/v0.10.1",
    );
  });
});
