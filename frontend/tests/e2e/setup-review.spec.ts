/** First-run review shows only checked choices and never submits credentials early. */
import { expect, test } from "@playwright/test";

const status = {
  configured: false,
  setup_available: true,
  user_count: 0,
  default_data_dir: "/data/files",
  default_thumb_dir: "/data/thumbs",
  current_data_dir: "/data/files",
  current_thumb_dir: "/data/thumbs",
  current_storage_backend: "local",
  current_s3_bucket: "",
  current_s3_endpoint_url: "",
  current_s3_region: "auto",
  current_backup_retention_days: 30,
  current_backup_s3_bucket: "",
  current_backup_s3_endpoint_url: "",
  current_backup_s3_region: "auto",
  configured_at: null,
};
const providers = [
  {
    id: "local",
    label: "Local disk",
    category: "this_machine",
    description: "Private directories on this machine.",
    expected_tier: "verified",
    expected_tier_note: "Local storage is verified after startup probes.",
    consequences: [],
    documentation_url: "/docs/storage-providers.md#local",
    available: true,
    selectable: true,
    fields: [
      {
        name: "data_dir",
        label: "Data directory",
        help: "Private model storage.",
        input_type: "path",
        required: true,
        secret: false,
      },
      {
        name: "thumb_dir",
        label: "Thumbnail directory",
        help: "Private thumbnail storage.",
        input_type: "path",
        required: true,
        secret: false,
      },
    ],
  },
];

test.describe("first-run setup review", () => {
  for (const [label, width, height] of [
    ["desktop", 1366, 900],
    ["phone", 390, 844],
  ] as const) {
    test(`reviews checked storage before account creation on ${label}`, async ({ page }) => {
      let completionRequests = 0;
      await page.setViewportSize({ width, height });
      await page.addInitScript(() => localStorage.clear());
      await page.route("**/api/v1/setup/status", (route) => route.fulfill({ json: status }));
      await page.route("**/api/v1/setup/session", (route) =>
        route.fulfill({ json: { csrf: "automatic-csrf", expires_in: 3600 } }),
      );
      await page.route("**/api/v1/storage/providers", (route) =>
        route.fulfill({ json: providers }),
      );
      await page.route("**/api/v1/setup/check-storage", (route) =>
        route.fulfill({ json: { ready: true, storage_provider: "local", checks: [] } }),
      );
      await page.route("**/api/v1/setup", (route) => {
        if (route.request().method() === "POST") completionRequests += 1;
        return route.fulfill({ status: 500, json: { detail: "not_expected" } });
      });
      await page.goto("/setup");
      await page.getByLabel("Username").fill("studio");
      await page.getByLabel("Password", { exact: true }).fill("example-password-123");
      await page.getByLabel("Confirm password").fill("example-password-123");
      await page.getByRole("button", { name: "Continue" }).click();
      await page.getByRole("button", { name: "Check storage" }).click();
      await expect(page.getByText("Storage ready")).toBeVisible();
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(
        page.getByRole("heading", { name: "Review before creating your account" }),
      ).toBeVisible();
      await expect(page.getByText("studio", { exact: true })).toBeVisible();
      await expect(page.getByText(/\/data\/files/)).toBeVisible();
      await expect(page.locator('input[type="password"]')).toHaveCount(0);
      await expect(page.getByText("example-password-123", { exact: true })).toHaveCount(0);
      expect(completionRequests).toBe(0);
      await page.getByRole("button", { name: "Edit" }).nth(2).click();
      await page.getByLabel("Data directory").fill("/data/new-files");
      await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    });
  }
});
