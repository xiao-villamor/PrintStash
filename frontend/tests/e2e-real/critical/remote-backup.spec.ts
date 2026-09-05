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

test.describe("partial backup recovery", () => {
  test("@critical retries the exact failed copy from partial backup success", async ({ page }) => {
    const { mkdir, writeFile, rm } = await import("node:fs/promises");
    const { dirname, resolve } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const { createHash } = await import("node:crypto");
    await page.goto("/login");
    await page.getByLabel("Username").fill("backup-admin");
    await page.getByLabel("Password", { exact: true }).fill("playwright-password");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/$/);

    const root = `blocked-replica-${Date.now()}`;
    const directory = resolve(dirname(fileURLToPath(import.meta.url)), "../.storage-data/webdav");
    await mkdir(directory, { recursive: true });
    const obstruction = resolve(directory, root);
    await writeFile(obstruction, "this file prevents creating the replica directory");
    try {
      const connection = await page.request.post("/api/v1/storage-connections", {
        data: {
          name: root,
          kind: "webdav",
          purpose: "backup",
          configuration: {
            provider: "webdav",
            endpoint_url: `http://127.0.0.1:${webdavPort}`,
            username: "backup-user",
            root,
          },
          secrets: { password: "backup-password" },
        },
      });
      expect(connection.status()).toBe(201);
      const selected = await page.request.put("/api/v1/config", {
        data: { manual_local_backup_enabled: true },
      });
      expect(selected.ok()).toBeTruthy();
      await page.goto("/settings?section=backup");
      const created = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/backups") && response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Backup now" }).click();
      const response = await created;
      expect(response.status()).toBe(202);
      const meta = await response.json();
      expect(meta.outcome).toBe("partial");
      const failed = meta.destination_results.find(
        (result: { name: string }) => result.name === root,
      );
      expect(failed.outcome).toBe("failed");
      const article = page.getByRole("article", { name: `${meta.backup_id}: Partially completed` });
      await expect(article.getByText(`${root} · Failed`)).toBeVisible();
      await expect(article.getByText("Local backup · Published")).toBeVisible();

      await rm(obstruction);
      const retried = page.waitForResponse(
        (candidate) =>
          candidate.url().endsWith(`/runs/destinations/${failed.id}/retry`) &&
          candidate.request().method() === "POST",
      );
      await article.getByRole("button", { name: "Retry this destination" }).click();
      expect((await retried).status()).toBe(200);
      const completed = page.getByRole("article", { name: `${meta.backup_id}: Completed` });
      await expect(completed.getByText(`${root} · Published`)).toBeVisible();
      await expect(completed.getByText(/Last verified:/)).toBeVisible();
      const remote = await page.request.get(
        `http://127.0.0.1:${webdavPort}/${failed.key.replace(/^webdav\//, "")}`,
      );
      expect(remote.ok()).toBeTruthy();
      expect(
        createHash("sha256")
          .update(await remote.body())
          .digest("hex"),
      ).toBe(meta.archive_sha256);
    } finally {
      await rm(obstruction, { force: true, recursive: true });
    }
  });
});
