/**
 * The storage setup headline flow crosses the browser, real API, process restart, and WebDAV.
 * It proves the provider selected during setup becomes the probed active provider afterward.
 */
import { writeFile } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";
import { clickModelAction, gcodeFor, modelCard, uploadGcodeModel } from "../util";

const apiPort = Number(process.env.PLAYWRIGHT_STORAGE_API_PORT ?? 8420);
const webdavPort = Number(process.env.PLAYWRIGHT_STORAGE_WEBDAV_PORT ?? 8775);
const nextcloudUrl = process.env.PLAYWRIGHT_STORAGE_NEXTCLOUD_URL;
const sftpHost = process.env.PLAYWRIGHT_STORAGE_SFTP_HOST;
const sftpPort = process.env.PLAYWRIGHT_STORAGE_SFTP_PORT ?? "2222";
const sftpUsername = process.env.PLAYWRIGHT_STORAGE_SFTP_USERNAME ?? "contract";
const sftpPassword = process.env.PLAYWRIGHT_STORAGE_SFTP_PASSWORD ?? "contract-only";
const sftpHostKey = process.env.PLAYWRIGHT_STORAGE_SFTP_HOST_KEY;
const apiBase = `http://127.0.0.1:${apiPort}`;
const webdavBase = `http://127.0.0.1:${webdavPort}`;
const restartTrigger = path.resolve("tests/e2e-real/.storage-data/restart");

const nextcloudUsername = "admin";
const nextcloudPassword = "contract-only";

async function nextcloudRemoteHrefs(page: Page): Promise<string[]> {
  if (!nextcloudUrl) return [];
  const response = await page.request.fetch(
    `${nextcloudUrl}/remote.php/dav/files/${nextcloudUsername}/vault-data`,
    {
      method: "PROPFIND",
      headers: {
        Authorization: `Basic ${Buffer.from(`${nextcloudUsername}:${nextcloudPassword}`).toString("base64")}`,
        Depth: "infinity",
      },
    },
  );
  if (!response.ok()) return [];
  const xml = await response.text();
  return [...xml.matchAll(/<(?:d|D):href>([^<]+)<\/(?:d|D):href>/g)].map((match) => match[1]);
}

async function webdavRemoteHrefs(page: Page): Promise<string[]> {
  const response = await page.request.fetch(`${webdavBase}/vault-data`, {
    method: "PROPFIND",
    headers: { Depth: "infinity" },
  });
  if (!response.ok()) return [];
  const xml = await response.text();
  return [...xml.matchAll(/<(?:d|D):href>([^<]+)<\/(?:d|D):href>/g)].map((match) => match[1]);
}

async function previewExpiredTrashWithoutApproval(page: Page, modelName: string): Promise<void> {
  await page.goto("/settings?section=trash");
  await expect(page.getByText(modelName)).toBeVisible();
  await page.getByRole("spinbutton").fill("0");
  await page.getByRole("button", { name: "Save retention" }).click();
  await page.getByRole("button", { name: "Review expired" }).click();
  const dialog = page.getByRole("dialog", { name: "Create a safe GC preview?" });
  await expect(dialog).toContainText("It does not delete catalog rows or storage bytes.");
  await dialog.getByRole("button", { name: "Create preview" }).click();

  await expect(page.getByText(/GC plan #\d+ · preview/)).toBeVisible();
  await expect(page.getByText(modelName)).toBeVisible();
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
  await expect(page.getByText(modelName)).toBeVisible();

  await page.getByRole("button", { name: "Abort plan" }).click();
  await expect(page.getByText(/GC plan #\d+ · aborted/)).toBeVisible();
}

test.describe("storage provider setup", () => {
  test.describe.configure({ mode: "serial" });

  test("@critical configures WebDAV through restart with safe GC preview", async ({ page }) => {
    await page.goto("/setup");
    await page.getByLabel("Username").fill("storage-admin");
    await page.getByLabel("Password", { exact: true }).fill("playwright-password");
    await page.getByLabel("Confirm password").fill("playwright-password");
    await page.getByRole("button", { name: "Continue" }).click();

    await page.getByText("View location and advanced options").click();
    await page.getByRole("button", { name: /Nextcloud and WebDAV/ }).click();
    await page.getByRole("button", { name: /^WebDAV/ }).click();
    await expect(page.getByText("Support: Beta")).toBeVisible();
    await expect(page.getByText("Expected: Guarded")).toBeVisible();
    await page.getByLabel("Server URL").fill(`http://127.0.0.1:${webdavPort}`);
    await page.getByLabel("Username").fill("webdav-user");
    await page.getByLabel("Password").fill("webdav-password");
    await page.getByLabel("Root").fill("vault-data");
    await page.getByRole("button", { name: "Check storage" }).click();
    await page.getByRole("button", { name: "Create my account and continue" }).click();
    await expect(page).toHaveURL(/\/getting-started$/);
    await page.getByRole("button", { name: "I'll do this later" }).click();

    await writeFile(restartTrigger, "restart\n", "utf8");
    await expect
      .poll(async () => {
        try {
          const response = await page.request.get(`${apiBase}/api/v1/health`);
          if (!response.ok()) return null;
          const health = await response.json();
          return {
            provider: health.storage?.provider,
            tier: health.storage?.tier,
          };
        } catch {
          return null;
        }
      })
      .toEqual({ provider: "webdav", tier: "guarded" });

    await page.goto("/settings?section=storage");
    await expect(page.getByRole("heading", { name: "Storage configuration" })).toBeVisible();
    await expect(page.getByText("Active: Guarded")).toBeVisible();
    await expect(page.getByPlaceholder("Stored — leave blank to keep")).toBeVisible();

    // Continue through the public UI after restart. This deliberately does not
    // bind a backend instance: the upload, trash, preview, and refusal
    // result must all cross the configured provider boundary.
    const modelName = `storage-lifecycle-${Date.now()}`;
    const remoteBefore = await webdavRemoteHrefs(page);
    await uploadGcodeModel(page, modelName);
    await expect
      .poll(async () => {
        const hrefs = await webdavRemoteHrefs(page);
        return hrefs.some((href) => !remoteBefore.includes(href) && /\.gcode(?:$|\?)/i.test(href));
      })
      .toBe(true);
    const remoteObject = (await webdavRemoteHrefs(page)).find(
      (href) => !remoteBefore.includes(href) && /\.gcode(?:$|\?)/i.test(href),
    );
    expect(remoteObject).toBeTruthy();
    const remoteObjectUrl = new URL(remoteObject!, webdavBase).toString();
    const expectedBytes = Buffer.from(gcodeFor(modelName));
    await modelCard(page, modelName).click();
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();

    await previewExpiredTrashWithoutApproval(page, modelName);
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    const purge = page.getByRole("dialog", { name: "Remove from catalog?" });
    await expect(purge).toContainText("Stored bytes are retained");
    const purged = page.waitForResponse(
      (response) => response.url().includes("/purge") && response.request().method() === "DELETE",
    );
    await purge.getByRole("button", { name: "Remove from catalog", exact: true }).click();
    expect((await purged).status()).toBe(200);
    await expect(page.getByText(modelName, { exact: true })).toHaveCount(0);
    const retainedBytes = await page.request.fetch(remoteObjectUrl);
    expect(retainedBytes.status()).toBe(200);
    expect(Number(retainedBytes.headers()["content-length"] ?? 0)).toBe(expectedBytes.length);
    expect(await retainedBytes.body()).toEqual(expectedBytes);
  });

  test("configures real Nextcloud with safe GC preview", async ({ page }) => {
    test.skip(
      !nextcloudUrl,
      "Set PLAYWRIGHT_STORAGE_NEXTCLOUD_URL and run the optional Nextcloud container to enable this contract.",
    );

    await page.goto("/settings?section=storage");
    await page.getByRole("button", { name: /Nextcloud and WebDAV/ }).click();
    await page.getByRole("button", { name: /^Nextcloud/ }).click();
    await page.getByLabel("Server URL").fill(nextcloudUrl!);
    await page.getByLabel("Username").fill(nextcloudUsername);
    await page.getByLabel("Password").fill(nextcloudPassword);
    await page.getByLabel("Root").fill("vault-data");
    await page.getByRole("button", { name: "Save configuration" }).click();

    await writeFile(restartTrigger, "restart\n", "utf8");
    await expect
      .poll(async () => {
        try {
          const response = await page.request.get(`${apiBase}/api/v1/health`);
          if (!response.ok()) return null;
          const health = await response.json();
          return { provider: health.storage?.provider, tier: health.storage?.tier };
        } catch {
          return null;
        }
      })
      .toEqual({ provider: "nextcloud", tier: "guarded" });

    const modelName = `nextcloud-storage-${Date.now()}`;
    const remoteBefore = await nextcloudRemoteHrefs(page);
    await uploadGcodeModel(page, modelName);
    await expect
      .poll(async () => {
        const hrefs = await nextcloudRemoteHrefs(page);
        return hrefs.some((href) => !remoteBefore.includes(href) && /\.gcode(?:$|\?)/i.test(href));
      })
      .toBe(true);
    const remoteObject = (await nextcloudRemoteHrefs(page)).find(
      (href) => !remoteBefore.includes(href) && /\.gcode(?:$|\?)/i.test(href),
    );
    expect(remoteObject).toBeTruthy();
    const remoteObjectUrl = new URL(remoteObject!, nextcloudUrl).toString();
    const uploadedBytes = await page.request.fetch(remoteObjectUrl, {
      headers: {
        Authorization: `Basic ${Buffer.from(`${nextcloudUsername}:${nextcloudPassword}`).toString("base64")}`,
      },
    });
    expect(uploadedBytes.status()).toBe(200);
    const expectedBytes = Buffer.from(gcodeFor(modelName));
    expect(Number(uploadedBytes.headers()["content-length"] ?? 0)).toBe(expectedBytes.length);
    expect(await uploadedBytes.body()).toEqual(expectedBytes);
    await modelCard(page, modelName).click();
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();

    await previewExpiredTrashWithoutApproval(page, modelName);
    const retainedBytes = await page.request.fetch(remoteObjectUrl, {
      headers: {
        Authorization: `Basic ${Buffer.from(`${nextcloudUsername}:${nextcloudPassword}`).toString("base64")}`,
      },
    });
    expect(retainedBytes.status()).toBe(200);
    expect(Number(retainedBytes.headers()["content-length"] ?? 0)).toBe(expectedBytes.length);
    expect(await retainedBytes.body()).toEqual(expectedBytes);
  });

  test("configures real SFTP with safe GC preview", async ({ page }) => {
    test.skip(
      !sftpHost || !sftpHostKey,
      "Set PLAYWRIGHT_STORAGE_SFTP_HOST and PLAYWRIGHT_STORAGE_SFTP_HOST_KEY for the optional OpenSSH harness.",
    );

    await page.goto("/settings?section=storage");
    await page.getByRole("button", { name: "NAS over SFTP" }).click();
    await page.getByRole("button", { name: /^SFTP/ }).click();
    await page.getByLabel("Host").fill(sftpHost!);
    await page.getByLabel("Port").fill(sftpPort);
    await page.getByLabel("Username").fill(sftpUsername);
    await page.getByLabel("Password").fill(sftpPassword);
    await page.getByLabel("Host key").fill(sftpHostKey!);
    await page.getByLabel("Root").fill("vault-data");
    await page.getByRole("button", { name: "Save configuration" }).click();

    await writeFile(restartTrigger, "restart\n", "utf8");
    await expect
      .poll(async () => {
        try {
          const response = await page.request.get(`${apiBase}/api/v1/health`);
          if (!response.ok()) return null;
          const health = await response.json();
          return { provider: health.storage?.provider, tier: health.storage?.tier };
        } catch {
          return null;
        }
      })
      .toEqual({ provider: "sftp", tier: "guarded" });

    const modelName = `sftp-storage-${Date.now()}`;
    await uploadGcodeModel(page, modelName);
    await modelCard(page, modelName).click();
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await previewExpiredTrashWithoutApproval(page, modelName);
  });
});
