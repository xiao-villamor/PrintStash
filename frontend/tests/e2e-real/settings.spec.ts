import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

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
  await page.getByRole("button", { name: "Storage" }).click();

  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith("/api/v1/backups") && r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "Backup now" }).click(),
  ]);

  // The new backup shows up in the Restore-backup list with a Download action.
  await expect(page.getByRole("button", { name: "Download" }).first()).toBeVisible();
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
  const version = (await (await page.request.get("/api/v1/health")).json()).version;
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
  await expect(page.getByRole("switch", { name: "Auto-mark known good on successful print" })).toHaveAttribute("aria-checked", after!);

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

test("purge-expired empties the trash", async ({ page }) => {
  const name = `e2e-purge-${Date.now()}`;
  await uploadGcodeModel(page, name);
  await modelCard(page, name).click();
  await page.getByTitle("Delete model").click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();

  await page.goto("/settings");
  await page.getByRole("button", { name: "Trash" }).click();
  await expect(page.getByText(name)).toBeVisible();

  // Retention 0 means everything already in trash is past expiry.
  await page.getByRole("spinbutton").fill("0");
  await page.getByRole("button", { name: "Save retention" }).click();
  await page.getByRole("button", { name: "Purge expired" }).click();
  await expect(page.getByText(name)).toHaveCount(0);

  // Restore the default so later runs aren't affected.
  await page.getByRole("spinbutton").fill("30");
  await page.getByRole("button", { name: "Save retention" }).click();
});
