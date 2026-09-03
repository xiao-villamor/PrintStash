/**
 * The settings surface, which is where a deployment is actually operated from.
 *
 * These flows have nothing in common except that an operator does them from one
 * page and each writes something durable: an API key that must work then stop working,
 * a currency every price is rendered in, an export that has to be a real file, a backup,
 * a notification channel, and the trash purge. Each is asserted after a reload or against
 * the artefact it produced, because "the toast appeared" is not evidence anything saved.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, uploadGcodeModel } from "./util";

test.describe("settings", () => {
  test("create and revoke an API key", async ({ page }) => {
    const keyName = `e2e-key-${Date.now()}`;
    await page.goto("/settings");
    await page.getByRole("button", { name: "Users & Access" }).click();

    // The key-name field is the input next to the Generate button (pre-filled).
    const keyField = page.getByLabel("Key name");
    await keyField.fill(keyName);
    await page.getByRole("button", { name: "Generate" }).click();

    // One-time secret is shown, and the key appears in the active list.
    await expect(page.getByText("It will only be shown once.")).toBeVisible();
    await expect(page.getByText(keyName)).toBeVisible();

    // Revoke it.
    await page.getByTitle("Revoke API key").click();
    await expect(page.getByText(keyName)).toHaveCount(0);
  });

  test("change display currency persists", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Design" }).click();

    const currency = page
      .getByRole("combobox")
      .filter({ has: page.getByRole("option", { name: "EUR — Euro (€)" }) });

    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/config") && r.request().method() === "PUT",
      ),
      currency.selectOption("EUR"),
    ]);

    await page.reload();
    await page.getByRole("button", { name: "Design" }).click();
    await expect(
      page
        .getByRole("combobox")
        .filter({ has: page.getByRole("option", { name: "EUR — Euro (€)" }) }),
    ).toHaveValue("EUR");

    // Restore default so the shared DB doesn't drift for later runs.
    await page
      .getByRole("combobox")
      .filter({ has: page.getByRole("option", { name: "USD — US Dollar ($)" }) })
      .selectOption("USD");
  });

  test("export library metadata as JSON", async ({ page }) => {
    await page.goto("/settings"); // Overview is the default section.
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /^JSON$/ }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.json$/);
  });

  test("create a manual backup", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Backup", exact: true }).click();

    await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/backups") && r.request().method() === "POST",
      ),
      page.getByRole("button", { name: "Backup now" }).click(),
    ]);

    // The new backup shows up in the Restore-backup list with a Download action.
    await expect(page.getByRole("button", { name: "Download" }).first()).toBeVisible();
  });

  test("upload an existing backup archive", async ({ page }) => {
    const createdResponse = await page.request.post("/api/v1/backups");
    expect(createdResponse.ok()).toBeTruthy();
    const metadata = await createdResponse.json();
    const source = new URLSearchParams({ source_ref: metadata.source_ref });
    const archiveResponse = await page.request.get(
      `/api/v1/backups/${metadata.backup_id}/download?${source}`,
    );
    expect(archiveResponse.ok()).toBeTruthy();
    const disposition = archiveResponse.headers()["content-disposition"] ?? "";
    const filename = disposition.match(/filename="?([^";]+)"?/)?.[1];
    expect(filename).toBeTruthy();
    const archive = await archiveResponse.body();

    const deleted = await page.request.delete(`/api/v1/backups/${metadata.backup_id}?${source}`);
    expect(deleted.ok()).toBeTruthy();

    await page.goto("/settings?section=backup");
    const uploadedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/backups/upload") && response.request().method() === "POST",
    );
    await page.getByLabel("Upload backup archive").setInputFiles({
      name: filename!,
      mimeType: "application/gzip",
      buffer: archive,
    });
    const uploaded = await uploadedResponse;
    expect(uploaded.status()).toBe(201);
    await expect(page.getByRole("button", { name: "Restore", exact: true }).first()).toBeVisible();
  });

  test("create one remote connection for backups and Library sources", async ({ page }) => {
    const connectionName = `e2e-remote-${Date.now()}`;
    await page.goto("/settings?section=remote-storage");

    await page.getByLabel("Connection name").fill(connectionName);
    await page.getByLabel("Bucket").fill("printstash-e2e");
    await page.getByLabel("Access key").fill("e2e-access");
    await page.getByLabel("Secret key").fill("e2e-secret");
    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/storage-connections") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Save connection" }).click();
    expect((await created).status()).toBe(201);

    const row = page.getByRole("listitem").filter({ hasText: connectionName });
    await expect(row).toBeVisible();
    await expect(row.getByRole("combobox", { name: `Use ${connectionName} for` })).toHaveValue(
      "both",
    );

    await row.getByRole("button", { name: "Remove" }).click();
    await page
      .getByRole("dialog", { name: "Remove remote connection?" })
      .getByRole("button", { name: "Remove connection" })
      .click();
    await expect(row).toHaveCount(0);
  });

  test("uses the exact source reference for a backup download", async ({ page }) => {
    await page.goto("/settings?section=backup");

    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/backups") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Backup now" }).click();
    const metadata = await (await created).json();
    expect(metadata.source_ref).toBeTruthy();

    const download = page.waitForRequest(
      (request) =>
        request.method() === "GET" &&
        request.url().includes("/api/v1/backups/") &&
        request.url().includes("/download?") &&
        new URL(request.url()).searchParams.get("source_ref") === metadata.source_ref,
    );
    await page.getByRole("button", { name: "Download" }).first().click();
    await download;
  });

  test("export library metadata as CSV", async ({ page }) => {
    await page.goto("/settings"); // Overview is the default section.
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /^CSV$/ }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });

  test("About shows the running version", async ({ page }) => {
    const version = (await (await page.request.get("/api/v1/health/details")).json()).version;
    await page.goto("/settings");
    await page.getByRole("button", { name: "About" }).click();
    await expect(page.getByText(`v${version}`).first()).toBeVisible();
  });

  test("overview shows server status and vault stats", async ({ page }) => {
    await page.goto("/settings"); // Overview is the default section.
    // System card: live health + storage backend from the real backend.
    await expect(page.getByText("Database", { exact: true })).toBeVisible();
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    await expect(page.getByText("Storage backend", { exact: true })).toBeVisible();
    await expect(page.getByText("LOCAL", { exact: true })).toBeVisible();
    // Stat cards render counts.
    await expect(page.getByText("Models", { exact: true })).toBeVisible();
    await expect(page.getByText("Collections", { exact: true })).toBeVisible();
  });

  test("restart returns the supervised API to a healthy state", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Restart PrintStash" }).click();

    const restartResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/system/restart") && response.request().method() === "POST",
    );
    await page
      .getByRole("dialog", { name: "Restart PrintStash?" })
      .getByRole("button", { name: "Restart now" })
      .click();
    expect((await restartResponse).status()).toBe(202);

    await expect
      .poll(
        async () => {
          try {
            return (await page.request.get("/api/v1/health")).ok();
          } catch {
            return false;
          }
        },
        { timeout: 15_000, intervals: [50, 100, 250] },
      )
      .toBe(false);
    await expect
      .poll(
        async () => {
          try {
            return (await page.request.get("/api/v1/health")).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30_000, intervals: [100, 250, 500] },
      )
      .toBe(true);
  });

  test("auto-mark-known-good toggle persists across reload", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Design" }).click();

    const sw = page.getByRole("switch", { name: "Auto-mark known good on successful print" });
    await expect(sw).toBeVisible();
    const before = await sw.getAttribute("aria-checked");

    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/config") && r.request().method() === "PUT",
      ),
      sw.click(),
    ]);
    const after = await sw.getAttribute("aria-checked");
    expect(after).not.toBe(before);

    await page.reload();
    await page.getByRole("button", { name: "Design" }).click();
    await expect(
      page.getByRole("switch", { name: "Auto-mark known good on successful print" }),
    ).toHaveAttribute("aria-checked", after!);

    // Restore the original so the shared DB doesn't drift for later runs.
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/config") && r.request().method() === "PUT",
      ),
      page.getByRole("switch", { name: "Auto-mark known good on successful print" }).click(),
    ]);
  });

  test("About shows the latest-release changelog", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "About" }).click();
    await expect(page.getByRole("heading", { name: "Latest changes" })).toBeVisible();
    await expect(page.getByText("What changed in the current release")).toBeVisible();
    // The release lists at least one change bullet.
    await expect(page.locator("ul > li").first()).toBeVisible();
  });

  test("add and delete a webhook notification channel", async ({ page }) => {
    const chName = `e2e-hook-${Date.now()}`;
    await page.goto("/settings");
    // Scope to main: the top bar also has an aria-label="Notifications" button.
    await page.getByRole("main").getByRole("button", { name: "Notifications" }).click();

    // Enable notifications if they aren't already (channel UI is gated on it).
    // The toggle round-trips to the backend before flipping, so click + poll
    // rather than check(), which expects an immediate state change.
    const enable = page.getByRole("checkbox").first();
    if (!(await enable.isChecked())) {
      await enable.click();
      await expect(enable).toBeChecked();
    }

    await page.getByRole("button", { name: "Add channel" }).click();
    await page.getByPlaceholder("Living-room printer alerts").fill(chName);
    await page.getByPlaceholder("https://example.com/hook").fill("https://example.com/e2e-hook");
    await page.getByRole("button", { name: "Create channel" }).click();

    // The channel persists and shows in the list; then delete it.
    await expect(page.getByText(chName)).toBeVisible();
    await page.getByTitle("Delete channel").click();
    await expect(page.getByText(chName)).toHaveCount(0);

    // Leave notifications disabled again so the shared DB doesn't drift.
    await enable.click();
    await expect(enable).not.toBeChecked();
  });

  test("expired GC preview is non-destructive without an independent backup", async ({ page }) => {
    const name = `e2e-purge-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await modelCard(page, name).click();
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();

    await page.goto("/settings");
    await page.getByRole("button", { name: "Trash" }).click();
    await expect(page.getByText(name)).toBeVisible();

    // Retention 0 means everything already in trash is past expiry.
    await page.getByRole("spinbutton").fill("0");
    await page.getByRole("button", { name: "Save retention" }).click();
    await page.getByRole("button", { name: "Review expired" }).click();
    const dialog = page.getByRole("dialog", { name: "Create a safe GC preview?" });
    await expect(dialog).toContainText("It does not delete catalog rows or storage bytes.");
    await dialog.getByRole("button", { name: "Create preview" }).click();

    await expect(page.getByText(/GC plan #\d+ · preview/)).toBeVisible();
    await expect(page.getByText(name)).toBeVisible();
    const digest = await page.getByText(/^[0-9a-f]{64}$/).textContent();
    expect(digest).not.toBeNull();
    await page.getByLabel("Confirm GC plan digest").fill(digest!);

    const refused = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/admin/gc/") &&
        response.url().endsWith("/approve") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Verify backup and quarantine" }).click();
    expect((await refused).status()).toBe(409);
    await expect(page.getByText(name)).toBeVisible();

    await page.getByRole("button", { name: "Abort plan" }).click();
    await expect(page.getByText(/GC plan #\d+ · aborted/)).toBeVisible();

    // Restore the default so later runs aren't affected.
    await page.getByRole("spinbutton").fill("30");
    await page.getByRole("button", { name: "Save retention" }).click();
  });
});
