/** Named presets configure their existing transport through the shared form. */
import { expect, test } from "@playwright/test";
import { storageProviderCatalogue } from "../../src/test-support/storage-provider-catalogue";
import { aStorageConnection } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";
useMockApi();
test.describe("storage presets", () => {
  for (const preset of [
    {
      id: "garage",
      category: "S3-compatible object storage",
      label: "Garage",
      fields: {
        Bucket: "models",
        Endpoint: "https://s3.example.test",
        "Access key": "test-access",
        "Secret key": "test-secret",
      },
      kind: "s3",
    },
    {
      id: "koofr",
      category: "Nextcloud and WebDAV",
      label: "Koofr — WebDAV",
      fields: { Username: "owner@example.test", Password: "app-password" },
      kind: "webdav",
    },
    {
      id: "hetzner_storage_box",
      category: "NAS over SFTP",
      label: "Hetzner Storage Box — SFTP",
      fields: {
        Host: "box.example.test",
        Username: "owner",
        "Host key": "/run/known_hosts",
        Password: "test-password",
      },
      kind: "sftp",
    },
  ]) {
    test(`saves ${preset.id} through its declared role`, async ({ page }) => {
      await page.route("**/api/v1/storage/providers", (route) =>
        route.fulfill({ json: storageProviderCatalogue }),
      );
      await page.route("**/api/v1/storage-connections", (route) =>
        route.fulfill({
          json:
            route.request().method() === "POST"
              ? aStorageConnection({ name: "Preset backup" })
              : [],
          status: route.request().method() === "POST" ? 201 : 200,
        }),
      );
      await page.goto("/settings?section=remote-storage");
      const remote = page.getByRole("region", { name: "Remote storage" });
      await remote.getByRole("button", { name: "Add remote connection" }).click();
      const form = page.getByRole("dialog", { name: "Add remote connection" });
      await form
        .getByRole("group", { name: "Storage category" })
        .getByRole("button", { name: preset.category })
        .click();
      await form
        .getByRole("group", { name: "Provider" })
        .getByRole("button", { name: preset.label })
        .click();
      await page.getByLabel("Connection name").fill("Preset backup");
      await form
        .getByRole("group", { name: "Use for" })
        .getByRole("button", { name: "Backup replicas" })
        .click();
      for (const [name, value] of Object.entries(preset.fields))
        await page.getByLabel(new RegExp(`^${name}(?:\\s*Optional)?$`)).fill(value);
      const saved = page.waitForRequest(
        (request) =>
          request.url().endsWith("/api/v1/storage-connections") && request.method() === "POST",
      );
      await page.getByRole("button", { name: "Save connection" }).click();
      expect((await saved).postDataJSON()).toMatchObject({
        kind: preset.kind,
        purpose: "backup",
        configuration: { provider: preset.id },
      });
      await expect(page.getByText("Preset backup", { exact: true })).toBeVisible();
    });
  }
});
