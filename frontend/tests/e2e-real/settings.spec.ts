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
  test("cache GB limits persist after reloading settings", async ({ page }) => {
    await page.goto("/settings?section=storage");
    const enabled = page.getByRole("checkbox", { name: "Keep downloaded files on this machine" });
    const wasEnabled = await enabled.isChecked();
    if (!wasEnabled) await enabled.click();
    const limit = page.getByRole("spinbutton", { name: "Cache size limit (GB)" });
    const free = page.getByRole("spinbutton", { name: "Keep free on disk (GB)" });
    const originalLimit = await limit.inputValue();
    const originalFree = await free.inputValue();
    await expect(page.getByText("Maximum cached files", { exact: true })).not.toBeVisible();
    await limit.fill("2.5");
    await free.fill("0.5");
    await page.getByRole("button", { name: "Save cache settings" }).click();
    await expect(page.getByText("Artifact cache settings updated.")).toBeVisible();
    await page.reload();
    await expect(limit).toHaveValue("2.5");
    await expect(free).toHaveValue("0.5");
    await page.getByText("Advanced cache settings", { exact: true }).click();
    await expect(page.getByRole("spinbutton", { name: "Maximum cached files" })).toBeVisible();
    await limit.fill(originalLimit);
    await free.fill(originalFree);
    if (!wasEnabled) await enabled.click();
    await page.getByRole("button", { name: "Save cache settings" }).click();
    await expect(page.getByText("Artifact cache settings updated.")).toBeVisible();
  });

  test("guides settings across screen sizes", async ({ page }, testInfo) => {
    for (const theme of ["light", "dark"]) {
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.goto("/settings?section=storage");
        await expect(page.getByRole("heading", { name: "Library storage" })).toBeVisible();
        await page.evaluate(
          (dark) => document.documentElement.classList.toggle("dark", dark),
          theme === "dark",
        );
        await expect(page.getByText("Library files", { exact: true })).toBeVisible();
        await expect(page.getByText("Growth forecast", { exact: true })).not.toBeVisible();
        await expect(
          page.getByRole("button", { name: "Clear cached files", exact: true }),
        ).toHaveCount(0);
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
        ).toBeLessThanOrEqual(0);
        await page.screenshot({
          path: testInfo.outputPath(`storage-${theme}-${width}.png`),
          fullPage: true,
          animations: "disabled",
        });
        await page.goto("/settings?section=ai-search");
        await expect(
          page.getByRole("heading", { name: "Where should AI Search run?" }),
        ).toBeVisible();
        await page.evaluate(
          (dark) => document.documentElement.classList.toggle("dark", dark),
          theme === "dark",
        );
        await expect(page.getByRole("combobox")).toHaveCount(0);
        await page.screenshot({
          path: testInfo.outputPath(`ai-${theme}-${width}.png`),
          fullPage: true,
          animations: "disabled",
        });
        await page.getByRole("button", { name: "Use this machine" }).click();
        await expect(page.getByRole("button", { name: "Change location" })).toBeVisible();
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
        ).toBeLessThanOrEqual(0);
        await page.getByRole("button", { name: "Change location" }).click();
        await page.getByRole("button", { name: "Connect another server" }).click();
        await expect(page.getByRole("form", { name: "Inference server" })).toBeVisible();
        await page.getByRole("button", { name: "Advanced AI controls" }).click();
        await expect(
          page.getByRole("checkbox", { name: "Enable AI Search", exact: true }),
        ).toBeVisible();
        await page.getByRole("button", { name: "Back to guided setup" }).click();
        await expect(
          page.getByRole("heading", { name: "Where should AI Search run?" }),
        ).toBeVisible();
      }
    }
    await page.goto("/settings?section=storage");
    await page.getByRole("button", { name: "Move storage with a verified migration" }).click();
    await expect(
      page.getByRole("heading", { name: "Move Vault storage", exact: true }),
    ).toBeVisible();
  });

  test("returns from advanced AI controls without searching the page", async ({
    page,
  }, testInfo) => {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto("/settings?section=ai-search");
      await page.getByRole("button", { name: "Connect another server" }).click();
      await page.getByRole("button", { name: "Advanced AI controls" }).click();
      await page
        .getByRole("checkbox", { name: "Enable AI Search", exact: true })
        .scrollIntoViewIfNeeded();
      const back = page.getByRole("button", { name: "Back to guided setup" });
      await expect(back).toBeInViewport();
      await expect(back).toBeFocused();
      await page.screenshot({
        path: testInfo.outputPath(`advanced-${width}.png`),
        animations: "disabled",
      });
      await page.keyboard.press("Enter");
      await expect(
        page.getByRole("heading", { name: "Where should AI Search run?" }),
      ).toBeInViewport();
      await expect(page.getByRole("button", { name: "Advanced AI controls" })).toBeFocused();
      await page.screenshot({
        path: testInfo.outputPath(`guided-return-${width}.png`),
        animations: "disabled",
      });
    }
  });

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

    const [created] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/backups") && r.request().method() === "POST",
      ),
      page.getByRole("button", { name: "Backup now" }).click(),
    ]);
    const metadata = await created.json();

    // The new backup shows up in the Restore-backup list with a Download action.
    const backupRow = page.locator("div.grid").filter({ hasText: metadata.backup_id }).last();
    await expect(backupRow.getByRole("button", { name: "Download" })).toBeVisible();

    await backupRow.getByRole("button", { name: "Delete backup" }).click();
    const deleted = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/backups/${metadata.backup_id}?`) &&
        response.request().method() === "DELETE",
    );
    await page
      .getByRole("dialog", { name: "Delete this backup copy?" })
      .getByRole("button", { name: "Delete backup" })
      .click();
    expect((await deleted).ok()).toBeTruthy();
    await expect(page.getByText(metadata.backup_id)).toHaveCount(0);
  });

  test("configure automatic backups with independent destinations", async ({ page }) => {
    const connectionName = `e2e-backup-policy-${Date.now()}`;
    const created = await page.request.post("/api/v1/storage-connections", {
      data: {
        name: connectionName,
        kind: "s3",
        purpose: "backup",
        configuration: {
          provider: "s3",
          bucket: "printstash-scheduled",
          root: "PrintStash",
          region: "us-east-1",
          endpoint_url: "",
          addressing_style: "auto",
        },
        secrets: { access_key: "e2e-access", secret_key: "e2e-secret" },
      },
    });
    expect(created.status()).toBe(201);
    const connection = await created.json();

    try {
      await page.goto("/settings?section=backup");
      await expect(page.getByLabel(`Use ${connectionName} for manual backups`)).toBeVisible();
      await page.getByLabel("Enable automatic backups").click();
      await page.getByLabel("Daily time (UTC)").fill("04:30");
      await page.getByLabel("Use local storage for manual backups").click();

      await Promise.all([
        page.waitForResponse(
          (response) =>
            response.url().endsWith("/api/v1/config") && response.request().method() === "PUT",
        ),
        page.waitForResponse(
          (response) =>
            response.url().endsWith(`/api/v1/storage-connections/${connection.id}`) &&
            response.request().method() === "PATCH",
        ),
        page.getByRole("button", { name: "Save backup settings" }).click(),
      ]);

      const [configResponse, connectionsResponse] = await Promise.all([
        page.request.get("/api/v1/config"),
        page.request.get("/api/v1/storage-connections"),
      ]);
      const config = await configResponse.json();
      const connections = await connectionsResponse.json();
      const saved = connections.find((item: { id: number }) => item.id === connection.id);
      expect(config.automatic_backups_enabled).toBe(true);
      expect(config.automatic_backup_time_utc).toBe("04:30");
      expect(config.manual_local_backup_enabled).toBe(false);
      expect(config.automatic_local_backup_enabled).toBe(true);
      expect(saved.manual_backup_enabled).toBe(true);
      expect(saved.automatic_backup_enabled).toBe(true);
    } finally {
      await page.request.put("/api/v1/config", {
        data: {
          automatic_backups_enabled: false,
          automatic_backup_time_utc: "02:00",
          manual_local_backup_enabled: true,
          automatic_local_backup_enabled: true,
        },
      });
      await page.request.delete(`/api/v1/storage-connections/${connection.id}`);
    }
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

  test("remote connection controls align at intermediate widths", async ({ page }) => {
    const connectionName = `e2e-remote-layout-${Date.now()}`;
    const created = await page.request.post("/api/v1/storage-connections", {
      data: {
        name: connectionName,
        kind: "s3",
        purpose: "both",
        configuration: {
          provider: "s3",
          bucket: "printstash-layout",
          root: "PrintStash",
          region: "us-east-1",
          endpoint_url: "",
          addressing_style: "auto",
        },
        secrets: { access_key: "e2e-access", secret_key: "e2e-secret" },
      },
    });
    expect(created.status()).toBe(201);
    const connection = await created.json();

    await page.setViewportSize({ width: 1180, height: 800 });
    await page.goto("/settings?section=remote-storage");
    const row = page.getByRole("listitem").filter({ hasText: connectionName });
    const usage = row.getByRole("combobox", { name: `Use ${connectionName} for` });
    const testButton = row.getByRole("button", { name: "Test" });
    await expect(row).toBeVisible();

    const [rowBox, usageBox, buttonBox] = await Promise.all([
      row.boundingBox(),
      usage.boundingBox(),
      testButton.boundingBox(),
    ]);
    expect(rowBox).not.toBeNull();
    expect(usageBox).not.toBeNull();
    expect(buttonBox).not.toBeNull();
    expect(
      Math.abs(usageBox!.y + usageBox!.height - (buttonBox!.y + buttonBox!.height)),
    ).toBeLessThan(2);
    expect(buttonBox!.x + buttonBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width);

    const removed = await page.request.delete(`/api/v1/storage-connections/${connection.id}`);
    expect(removed.status()).toBe(204);
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

  test("audit schedule persists after reload", async ({ page }) => {
    // The exact navigation path carried by storage notification payloads.
    await page.goto("/settings?section=maintenance");
    const form = page.getByRole("form", { name: "Quick audit schedule" });
    const enabled = form.getByRole("checkbox", { name: "Enabled" });
    await form.getByLabel("Frequency").selectOption("monthly");
    await form.getByLabel("Issue notification threshold").selectOption("critical");
    await form.getByLabel("Overdue after (minutes)").fill("45");
    if ((await enabled.getAttribute("aria-checked")) !== "true") await enabled.click();
    const paused = form.getByRole("checkbox", { name: "Paused" });
    if ((await paused.getAttribute("aria-checked")) !== "true") await paused.click();
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/maintenance/audit-policies/quick") &&
          response.request().method() === "PUT" &&
          response.ok(),
      ),
      form.getByRole("button", { name: "Save schedule" }).click(),
    ]);
    await page.reload();
    await expect(form.getByRole("checkbox", { name: "Enabled" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expect(form.getByRole("checkbox", { name: "Paused" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expect(form.getByLabel("Frequency")).toHaveValue("monthly");
    await expect(form.getByLabel("Issue notification threshold")).toHaveValue("critical");
    await expect(form.getByLabel("Overdue after (minutes)")).toHaveValue("45");
    await form.getByLabel("Frequency").selectOption("weekly");
    await form.getByLabel("Issue notification threshold").selectOption("warning");
    await form.getByLabel("Overdue after (minutes)").fill("120");
    // Restore the safe default in the shared backend after proving persistence.
    await enabled.click();
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/maintenance/audit-policies/quick") &&
          response.request().method() === "PUT" &&
          response.ok(),
      ),
      form.getByRole("button", { name: "Save schedule" }).click(),
    ]);
  });

  test("requires explicit cleanup from storage insights", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Storage", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Storage insights" })).toBeVisible();
    const [measurement] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/storage/inventory/sample") &&
          response.request().method() === "POST",
      ),
      page.getByRole("button", { name: "Refresh measurement" }).click(),
    ]);
    expect(measurement.status()).toBe(200);
    await expect(page.getByText(/Provider measurement:.*Capacity evidence is known/)).toBeVisible();
    await page.getByRole("button", { name: "Clean up expired staging" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Uncertain files are retained");
    await dialog.getByRole("button", { name: "Clean up", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "expired leases cleared" }),
    ).toBeVisible();
  });
});
