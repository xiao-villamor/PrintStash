/** A browser-created remote-only backup restores a purged Model and its bytes. */
import { expect, test } from "@playwright/test";

import { clickModelAction, gcodeFor, modelCard, uploadGcodeModel } from "../util";

const webdavPort = Number(process.env.PLAYWRIGHT_CRITICAL_BACKUP_WEBDAV_PORT ?? 8776);

test.describe("remote-only backup recovery", () => {
  test("@critical creates then restores a remote-only backup in the browser", async ({ page }) => {
    await page.goto("/setup");
    await page.getByLabel("Setup token").fill("playwright-critical-backup-token");
    await page.getByLabel("Username").fill("backup-admin");
    await page.getByLabel("Password", { exact: true }).fill("playwright-password");
    await page.getByLabel("Confirm password").fill("playwright-password");
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByRole("button", { name: "Complete setup" }).click();
    await expect(page).toHaveURL(/\/$/);

    const destinationName = `WebDAV backup ${Date.now()}`;
    await page.goto("/settings?section=remote-storage");
    await page.getByLabel("Connection name").fill(destinationName);
    await page.getByLabel("Provider").selectOption("webdav");
    await page
      .locator("label")
      .filter({ hasText: "Use for" })
      .last()
      .locator("select")
      .selectOption("backup");
    await page.getByLabel("Base folder").fill(`backup-data-${Date.now()}`);
    await page.getByLabel("WebDAV endpoint").fill(`http://127.0.0.1:${webdavPort}`);
    await page.getByLabel("Username").fill("backup-user");
    await page.getByLabel("Password").fill("backup-password");
    await page.getByRole("button", { name: "Save connection" }).click();
    await expect(page.getByText("Remote storage connection saved.")).toBeVisible();

    await page.goto("/settings?section=backup");
    await page.getByLabel("Use local storage for manual backups").uncheck();
    await page.getByRole("button", { name: "Save backup settings" }).click();

    const modelName = `remote-browser-recovery-${Date.now()}`;
    const expectedBytes = Buffer.from(gcodeFor(modelName));
    await uploadGcodeModel(page, modelName);
    const models = await (await page.request.get("/api/v1/models")).json();
    const model = models.find((item: { name: string }) => item.name === modelName);
    expect(model).toBeTruthy();

    await page.goto("/settings?section=backup");
    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/backups") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Backup now" }).click();
    const metadata = await (await created).json();
    expect(metadata.location).toBe("opendal:webdav");

    await page.goto("/");
    await modelCard(page, modelName).click();
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await page.goto("/settings?section=trash");
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("button", { name: "Delete forever" }).click();
    await expect
      .poll(async () => (await page.request.get(`/api/v1/models/${model.id}`)).status())
      .toBe(404);

    await page.goto("/settings?section=backup");
    const restored = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/backups/${metadata.backup_id}/restore`) &&
        response.request().method() === "POST",
    );
    await page
      .locator("div.grid")
      .filter({ hasText: metadata.backup_id })
      .last()
      .getByRole("button", { name: "Restore", exact: true })
      .click();
    await page
      .getByRole("dialog", { name: "Restore backup?" })
      .getByRole("button", { name: "Restore", exact: true })
      .click();
    expect((await restored).status()).toBe(200);

    const detail = await (await page.request.get(`/api/v1/models/${model.id}`)).json();
    const download = await page.request.get(`/api/v1/files/${detail.files[0].id}/download`);
    expect(download.ok()).toBeTruthy();
    expect(await download.body()).toEqual(expectedBytes);
  });
});
