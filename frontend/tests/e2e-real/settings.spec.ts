/**
 * The settings surface, which is where a deployment is actually operated from.
 *
 * These flows have nothing in common except that an operator does them from one
 * page and each writes something durable: an API key that must work then stop working,
 * a currency every price is rendered in, an export that has to be a real file, a backup,
 * a notification channel, and the trash purge. Each is asserted after a reload or against
 * the artefact it produced, because "the toast appeared" is not evidence anything saved.
 */
import { existsSync } from "node:fs";

import { test, expect, type Page } from "./helpers";
import {
  backupFromAccepted,
  clickModelAction,
  createBackupViaApi,
  modelCard,
  seedExpiredStaging,
  uploadGcodeModel,
} from "./util";
import { aSimilarityCandidate } from "../../src/test-support/similarity";
import { anArtifactCache } from "../../src/test-support/factories";
import type { StorageInventoryReport } from "../../src/lib/api/storage-inventory";

const mebibyte = 1024 ** 2;
const storageInsightsExample: StorageInventoryReport = {
  inventory: {
    schema_version: 1,
    generated_at: "2026-09-24T18:22:00Z",
    measured_at: "2026-09-24T18:22:00Z",
    target_ref: "local",
    logical_bytes: 708.7 * mebibyte,
    external_referenced_bytes: 0,
    unique_owned_bytes: 708.7 * mebibyte,
    unknown_object_count: 283,
    temporary_bytes: 0,
    backup_bytes: 0,
    measured_provider_bytes: null,
    method: "database ownership census",
    confidence: "known sizes only",
    provider_capacity: {
      status: "available",
      total_bytes: 16 * 1024 ** 3,
      used_bytes: 6.8 * 1024 ** 3,
      available_bytes: 9.2 * 1024 ** 3,
      quota_bytes: null,
      measured_at: "2026-09-24T18:22:00Z",
      method: "filesystem_statvfs",
      reliability: "exact",
      error: null,
    },
    latest_audit: null,
    buckets: [
      {
        category: "step",
        lifecycle: "live",
        count: 5,
        logical_bytes: 7.1 * mebibyte,
        external_bytes: 0,
      },
      {
        category: "stl",
        lifecycle: "live",
        count: 100,
        logical_bytes: 559.8 * mebibyte,
        external_bytes: 0,
      },
      {
        category: "3mf",
        lifecycle: "live",
        count: 20,
        logical_bytes: 68.1 * mebibyte,
        external_bytes: 0,
      },
      {
        category: "thumbnail",
        lifecycle: "owned",
        count: 70,
        logical_bytes: 4.4 * mebibyte,
        external_bytes: 0,
      },
      {
        category: "stl_cache",
        lifecycle: "owned",
        count: 80,
        logical_bytes: 69.3 * mebibyte,
        external_bytes: 0,
      },
    ],
    volumes: [
      {
        domain_id: "local",
        roles: ["staging", "backup", "vault", "derivatives"],
        total_bytes: 16 * 1024 ** 3,
        free_bytes: 9.2 * 1024 ** 3,
        reserved_bytes: 0,
        headroom_bytes: 1024 ** 3,
        status: "available",
        method: "filesystem_statvfs",
      },
    ],
  },
  history: [
    {
      sampled_at: "2026-09-12T00:00:00Z",
      owned_bytes: 120 * mebibyte,
      categories: {
        live_originals: 100 * mebibyte,
        trash: 0,
        derived_cache: 20 * mebibyte,
        backups: 0,
      },
    },
    {
      sampled_at: "2026-09-14T00:00:00Z",
      owned_bytes: 225 * mebibyte,
      categories: {
        live_originals: 180 * mebibyte,
        trash: 0,
        derived_cache: 45 * mebibyte,
        backups: 0,
      },
    },
    {
      sampled_at: "2026-09-18T00:00:00Z",
      owned_bytes: 505 * mebibyte,
      categories: {
        live_originals: 455 * mebibyte,
        trash: 0,
        derived_cache: 50 * mebibyte,
        backups: 0,
      },
    },
    {
      sampled_at: "2026-09-24T00:00:00Z",
      owned_bytes: 708.7 * mebibyte,
      categories: {
        live_originals: 635 * mebibyte,
        trash: 0,
        derived_cache: 73.7 * mebibyte,
        backups: 0,
      },
    },
  ],
  forecast: {
    status: "insufficient_data",
    days_remaining: null,
    bytes_per_day: null,
    sample_count: 4,
    window_days: 12,
    confidence: "insufficient",
    threshold_at: null,
  },
};

async function openAiSearchSettings(page: Page) {
  const ready = page.getByRole("tab", { name: "Guided setup" });
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.goto("/settings?section=ai-search", { waitUntil: "domcontentloaded" });
    try {
      await expect(ready).toBeVisible({ timeout: 15_000 });
      return;
    } catch (error) {
      if (attempt === 2) throw error;
    }
  }
}

test.describe("settings", () => {
  test("loads maintenance read APIs", async ({ page }) => {
    const apiPort = Number(process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410);
    await page.goto("/settings?section=maintenance", { waitUntil: "domcontentloaded" });
    for (const path of [
      "/api/v1/config/artifact-cache",
      "/api/v1/maintenance/audit-policies",
      "/api/v1/maintenance/audits",
    ]) {
      const response = await page.request.get(`http://127.0.0.1:${apiPort}${path}`);
      expect(response.status(), `${path}: ${await response.text()}`).toBe(200);
      const browserResponse = await page.evaluate(async (url) => {
        const result = await fetch(url, { credentials: "include" });
        return { status: result.status, body: await result.text() };
      }, path);
      expect(browserResponse.status, `${path} through Vite: ${browserResponse.body}`).toBe(200);
    }
  });

  test("renders the responsive storage settings workflow", async ({ page }, testInfo) => {
    await page.route("**/api/v1/storage/inventory/activity", (route) =>
      route.fulfill({
        json: {
          active_reservations: [
            {
              operation_kind: "backup",
              required_bytes: 8 * 1024 ** 2,
              roles: ["backups"],
              durable: false,
              created_at: "2026-09-24T18:00:00Z",
              expires_at: "2026-09-24T19:00:00Z",
            },
          ],
          recent_denials: [
            {
              operation_kind: "import",
              reason: "insufficient_space",
              required_bytes: 2 * 1024 ** 3,
              available_bytes: 1024 ** 3,
              reserved_bytes: 0,
              headroom_bytes: 0,
              occurred_at: "2026-09-24T17:00:00Z",
            },
          ],
        },
      }),
    );
    await page.route("**/api/v1/storage/inventory", (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({ json: storageInsightsExample });
    });
    await page.route("**/api/v1/storage/inventory/cleanup-opportunities", (route) =>
      route.fulfill({
        json: [
          {
            owner: "cache",
            candidate_count: 6,
            candidate_bytes: 69.3 * 1024 ** 2,
            action: "cleanup_derived_cache",
            available: true,
          },
        ],
      }),
    );
    const cachePolicy = anArtifactCache().policy;
    const cacheExample = anArtifactCache({
      policy: {
        ...cachePolicy,
        enabled: true,
        max_bytes: 10 * 1024 ** 3,
        headroom_bytes: 1024 ** 3,
      },
      usage: {
        bytes: 256 * 1024 ** 2,
        entries: 12,
        hit_ratio_percent: 72,
        bytes_saved: 2 * 1024 ** 3,
      },
    });
    await page.route("**/api/v1/config/artifact-cache", (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({ json: cacheExample });
    });
    await page.goto("/settings?section=storage", { waitUntil: "domcontentloaded" });
    const move = page.getByRole("region", { name: "Move Vault storage" });
    const cache = page.getByRole("heading", { name: "Remote file cache" });
    const cacheTabs = page
      .getByRole("tablist")
      .filter({ has: page.getByRole("tab", { name: "Cache limits" }) });
    const insightsCard = page
      .getByRole("heading", { name: "Storage insights" })
      .locator('xpath=ancestor::*[contains(@class,"overflow-hidden")][1]');
    await expect(page.getByRole("button", { name: "Move storage" })).toBeVisible({
      timeout: 45_000,
    });
    await expect(
      page.getByRole("region", { name: "Storage connection details" }).locator("dd"),
    ).toHaveCount(2);
    await expect(page.getByText("Files stored here")).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText("Temporary files").locator("xpath=..")).toContainText("0 MB");
    await expect(page.getByRole("region", { name: "What uses space" })).toContainText("635 MB");
    await expect(page.getByRole("img", { name: "Recorded owned storage over time" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Free up space" })).toContainText("69.3 MB");
    await expect(move.getByRole("heading", { name: "Destination storage" })).toBeVisible({
      timeout: 45_000,
    });
    await expect(cache).toBeVisible();
    for (const viewport of [
      { width: 1920, height: 1080 },
      { width: 390, height: 900 },
    ]) {
      const { width } = viewport;
      await page.setViewportSize(viewport);
      await page.evaluate(() => document.documentElement.classList.add("dark"));
      await page.getByRole("heading", { name: "Settings" }).scrollIntoViewIfNeeded();
      await page.screenshot({
        path: testInfo.outputPath(`storage-top-${width}.png`),
        fullPage: true,
      });
      await page
        .getByRole("region", { name: "Storage connection details" })
        .scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`storage-connection-${width}.png`) });

      await page.getByRole("heading", { name: "Storage insights" }).scrollIntoViewIfNeeded();
      await insightsCard.screenshot({ path: testInfo.outputPath(`storage-summary-${width}.png`) });
      if (width === 390) {
        await page.getByRole("region", { name: "What uses space" }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath("storage-breakdown-mobile.png") });
      }
      await page.getByText("Measurement details").click();
      await expect(page.getByRole("heading", { name: "Files by type" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Recent storage activity" })).toContainText(
        "backup",
      );
      await page.getByRole("heading", { name: "Storage insights" }).scrollIntoViewIfNeeded();
      await insightsCard.screenshot({ path: testInfo.outputPath(`storage-details-${width}.png`) });
      if (width === 390) {
        await page.getByRole("heading", { name: "Files by type" }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath("storage-measurement-mobile.png") });
        await page.getByText("Recent storage activity").scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath("storage-activity-mobile.png") });
      }
      await page.getByText("Measurement details").click();

      await move.screenshot({ path: testInfo.outputPath(`move-${width}.png`) });
      await move.getByText("Migration policy · Advanced settings").click();
      await move.screenshot({ path: testInfo.outputPath(`move-advanced-${width}.png`) });
      await move.getByText("Migration policy · Advanced settings").click();
      await move.getByRole("button", { name: "S3-compatible object storage" }).click();
      await move.screenshot({ path: testInfo.outputPath(`move-s3-${width}.png`) });
      await move.getByRole("button", { name: "This machine" }).click();

      await expect(
        page.getByRole("checkbox", { name: "Enable remote Artifact cache" }),
      ).toBeVisible({ timeout: 30_000 });
      await cacheTabs.getByRole("tab", { name: "Overview" }).click();
      await expect(page.getByRole("tabpanel", { name: "Overview" })).toBeVisible();
      await cache.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`cache-overview-${width}.png`) });
      await cacheTabs.getByRole("tab", { name: "Activity" }).click();
      await page.getByRole("heading", { name: "Remote file cache" }).scrollIntoViewIfNeeded();
      await page
        .getByRole("heading", { name: "Remote file cache" })
        .locator('xpath=ancestor::*[contains(@class,"rounded-lg")][1]')
        .screenshot({ path: testInfo.outputPath(`cache-advanced-${width}.png`) });
      await cacheTabs.getByRole("tab", { name: "Cache limits" }).click();
      await expect(page.getByRole("spinbutton", { name: "Maximum cache size (GB)" })).toHaveValue(
        "10",
      );
      await cache.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`cache-limits-${width}.png`) });
      if (width === 390) {
        const cacheSafetyNote = page.getByText(
          "Files in use stay available until their active reads finish. Clearing does not remove your Artifacts.",
        );
        await cacheSafetyNote.scrollIntoViewIfNeeded();
        await expect(cacheSafetyNote).toBeVisible();
      }
    }
  });

  test("navigates collection storage at both viewport sizes", async ({ page }, testInfo) => {
    await page.route("**/api/v1/storage/inventory", (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({ json: storageInsightsExample });
    });
    const collectionRows = [
      {
        collection_id: null,
        name: "Uncollected",
        logical_bytes: 251 * 1024 ** 2,
        external_bytes: 0,
        model_count: 3,
      },
      {
        collection_id: 1,
        name: "Garden tools",
        logical_bytes: 229 * 1024 ** 2,
        external_bytes: 0,
        model_count: 8,
      },
      {
        collection_id: 2,
        name: "Planters",
        logical_bytes: 74 * 1024 ** 2,
        external_bytes: 0,
        model_count: 4,
      },
      {
        collection_id: 3,
        name: "Desk parts",
        logical_bytes: 22 * 1024 ** 2,
        external_bytes: 0,
        model_count: 5,
      },
      {
        collection_id: 4,
        name: "Fixtures",
        logical_bytes: 16 * 1024 ** 2,
        external_bytes: 0,
        model_count: 2,
      },
      {
        collection_id: 5,
        name: "Tools",
        logical_bytes: 8 * 1024 ** 2,
        external_bytes: 0,
        model_count: 2,
      },
      {
        collection_id: 6,
        name: "Small parts",
        logical_bytes: 11 * 1024 ** 2,
        external_bytes: 0,
        model_count: 2,
      },
      {
        collection_id: 7,
        name: "Cable clips",
        logical_bytes: 9 * 1024 ** 2,
        external_bytes: 0,
        model_count: 1,
      },
      {
        collection_id: 8,
        name: "Adapters",
        logical_bytes: 8 * 1024 ** 2,
        external_bytes: 0,
        model_count: 1,
      },
      {
        collection_id: 9,
        name: "Rack case",
        logical_bytes: 6 * 1024 ** 2,
        external_bytes: 0,
        model_count: 1,
      },
      {
        collection_id: 10,
        name: "house",
        logical_bytes: 4.3 * 1024 ** 2,
        external_bytes: 0,
        model_count: 1,
      },
      {
        collection_id: 11,
        name: "Trash Bag Holder",
        logical_bytes: 3.9 * 1024 ** 2,
        external_bytes: 0,
        model_count: 1,
      },
    ];
    await page.route("**/api/v1/storage/inventory/collections*", (route) => {
      const offset = Number(new URL(route.request().url()).searchParams.get("offset") ?? 0);
      return route.fulfill({ json: collectionRows.slice(offset, offset + 11) });
    });
    await page.route("**/api/v1/storage/inventory/models*", (route) => {
      expect(new URL(route.request().url()).searchParams.get("collection_id")).toBe("0");
      return route.fulfill({
        json: [
          { model_id: 9, name: "Bracket", logical_bytes: 200 * 1024 ** 2 },
          { model_id: 10, name: "Spacer", logical_bytes: 50 * 1024 ** 2 },
          { model_id: 11, name: "Clip", logical_bytes: 1024 ** 2 },
        ],
      });
    });

    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/settings?section=storage", { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("heading", { name: "Storage insights" })).toBeVisible({
        timeout: 45_000,
      });
      await expect(page.getByRole("button", { name: /Uncollected/ })).toBeVisible({
        timeout: 45_000,
      });
      await expect(page.getByText("251 MB")).toBeVisible();
      await expect(page.getByRole("button", { name: /Uncollected/ })).toContainText("3 models");
      await page.evaluate(() => document.documentElement.classList.add("dark"));
      const collections = page
        .getByRole("heading", { name: "Collection storage" })
        .locator("xpath=ancestor::section[1]");
      await collections.screenshot({ path: testInfo.outputPath(`collection-sizes-${width}.png`) });
      const collectionHeight = await collections.evaluate(
        (section) => section.getBoundingClientRect().height,
      );
      const firstCollection = await collections
        .getByRole("button", { name: /Uncollected/ })
        .boundingBox();
      const secondCollection = await collections
        .getByRole("button", { name: /Garden tools/ })
        .boundingBox();
      expect(firstCollection).not.toBeNull();
      expect(secondCollection).not.toBeNull();
      if (firstCollection && secondCollection) {
        if (width === 1280) expect(secondCollection.x).toBeGreaterThan(firstCollection.x);
        else expect(secondCollection.y).toBeGreaterThan(firstCollection.y);
      }
      await page.getByRole("button", { name: /Uncollected/ }).click();
      const models = collections.getByRole("region", { name: "Models in Uncollected" });
      await expect(models.getByText("Bracket")).toBeVisible();
      await expect(models.getByText("Spacer")).toBeVisible();
      expect(
        await collections.evaluate((section) => section.getBoundingClientRect().height),
      ).toBeCloseTo(collectionHeight, 0);
      await expect(collections.getByRole("button", { name: /Garden tools/ })).toHaveCount(0);
      await expect(models.getByRole("link", { name: /Bracket/ })).toHaveAttribute(
        "href",
        "/models/9",
      );
      await expect(models.getByRole("button", { name: "All collections" })).toBeVisible();
      await models.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`collection-model-view-${width}.png`) });
      await models.screenshot({ path: testInfo.outputPath(`collection-models-${width}.png`) });
      await models.getByRole("button", { name: "All collections" }).click();
      await expect(models).toHaveCount(0);
      expect(
        await collections.evaluate((section) => section.getBoundingClientRect().height),
      ).toBeCloseTo(collectionHeight, 0);
      await collections.getByRole("button", { name: "Next page" }).click();
      await expect(collections.getByRole("button", { name: /house/ })).toContainText("4.3 MB");
      await collections.screenshot({ path: testInfo.outputPath(`collection-page-2-${width}.png`) });
      if (width === 390) {
        await collections.getByRole("button", { name: /house/ }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath("collection-page-2-mobile.png") });
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
      ).toBeLessThanOrEqual(1);
    }
  });

  test("keeps every model row above the pager in a large collection", async ({
    page,
  }, testInfo) => {
    const collectionName = "Workstation System Stackable Part Drawer Organizer";
    const modelRows = Array.from({ length: 17 }, (_, index) => ({
      model_id: index + 100,
      name: `Drawer part ${index + 1}`,
      logical_bytes: (17 - index) * 1024,
    }));
    await page.route("**/api/v1/storage/inventory", (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({ json: storageInsightsExample });
    });
    await page.route("**/api/v1/storage/inventory/collections*", (route) =>
      route.fulfill({
        json: [
          {
            collection_id: 42,
            name: collectionName,
            logical_bytes: 1024 ** 2,
            external_bytes: 0,
            model_count: modelRows.length,
          },
        ],
      }),
    );
    await page.route("**/api/v1/storage/inventory/models*", (route) => {
      const url = new URL(route.request().url());
      expect(url.searchParams.get("collection_id")).toBe("42");
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 0);
      return route.fulfill({ json: modelRows.slice(offset, offset + limit) });
    });

    for (const width of [1280, 390, 320]) {
      const pageSize = width >= 640 ? 8 : 5;
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/settings?section=storage", { waitUntil: "domcontentloaded" });
      const collections = page
        .getByRole("heading", { name: "Collection storage" })
        .locator("xpath=ancestor::section[1]");
      await expect(
        collections.getByRole("button", { name: new RegExp(collectionName) }),
      ).toBeVisible({
        timeout: 45_000,
      });
      const panelHeight = await collections.evaluate(
        (section) => section.getBoundingClientRect().height,
      );
      await collections.getByRole("button", { name: new RegExp(collectionName) }).click();
      const models = collections.getByRole("region", { name: `Models in ${collectionName}` });
      await expect(
        models.getByRole("link", { name: new RegExp(`Drawer part ${pageSize}\\b`) }),
      ).toBeVisible();
      await expect(collections.getByText(`Showing models 1–${pageSize}`)).toBeVisible();
      const pager = collections.getByRole("button", { name: "Next page" });
      await collections.screenshot({
        path: testInfo.outputPath(`large-collection-models-${width}.png`),
      });
      const lastRow = await models
        .getByRole("link", { name: new RegExp(`Drawer part ${pageSize}\\b`) })
        .boundingBox();
      const pagerBox = await pager.boundingBox();
      expect(lastRow).not.toBeNull();
      expect(pagerBox).not.toBeNull();
      if (lastRow && pagerBox)
        expect(lastRow.y + lastRow.height, `last model row at ${width}px`).toBeLessThan(pagerBox.y);
      expect(
        await models.evaluate((region) => {
          const scrollArea = region.parentElement;
          return scrollArea ? scrollArea.scrollHeight - scrollArea.clientHeight : Infinity;
        }),
      ).toBeLessThanOrEqual(1);
      for (let offset = pageSize; offset < modelRows.length; offset += pageSize) {
        await pager.click();
        const last = Math.min(offset + pageSize, modelRows.length);
        await expect(
          models.getByRole("link", { name: new RegExp(`Drawer part ${last}\\b`) }),
        ).toBeVisible();
        await expect(collections.getByText(`Showing models ${offset + 1}–${last}`)).toBeVisible();
      }
      if (width === 1280) {
        await page.setViewportSize({ width: 390, height: 900 });
        await expect(collections.getByText("Showing models 1–5")).toBeVisible();
        await page.setViewportSize({ width: 1280, height: 900 });
        await expect(collections.getByText("Showing models 1–8")).toBeVisible();
      }
      expect(
        await collections.evaluate((section) => section.getBoundingClientRect().height),
      ).toBeCloseTo(panelHeight, 0);
    }
  });

  test("renders maintenance controls at both viewport sizes", async ({ page }, testInfo) => {
    const policyStatuses: number[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/maintenance/audit-policies"))
        policyStatuses.push(response.status());
    });
    await page.route("**/api/v1/maintenance/audits", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/v1/backups/sources", (route) =>
      route.fulfill({
        json: [
          {
            backup_id: "2026-09-24T000000Z",
            created_at: "2026-09-24T00:00:00Z",
            location: "local",
            app_version: "0.13.0",
            file_count: 42,
            size_bytes: 1024,
            storage_backend: "local",
            source_ref: "local-source",
            provider_ref: "local",
            namespace: "vault-backups",
          },
        ],
      }),
    );
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/settings?section=maintenance", { waitUntil: "domcontentloaded" });
      const form = page.getByRole("form", { name: "Quick check schedule" });
      const full = page.getByRole("form", { name: "Full check schedule" });
      await expect(page.getByRole("button", { name: "Run quick check" })).toBeVisible({
        timeout: 45_000,
      });
      await expect(form.or(page.getByText("Could not load audit schedules."))).toBeVisible({
        timeout: 45_000,
      });
      expect(policyStatuses, "audit policy API responses").toContain(200);
      await expect(form).toBeVisible({ timeout: 45_000 });
      await expect(full).toBeVisible({ timeout: 45_000 });
      const backupCard = page
        .getByRole("heading", { name: "Check a backup" })
        .locator("xpath=../..");
      await expect(backupCard.getByRole("button", { name: "Verify" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Similar models" })).toHaveCount(0);
      await page.getByRole("heading", { name: "Check your files now" }).scrollIntoViewIfNeeded();
      await page.screenshot({
        path: testInfo.outputPath(`maintenance-${width}.png`),
        fullPage: true,
      });
      await form.getByRole("checkbox", { name: "Run automatically" }).click();
      await expect(form.getByLabel("Frequency")).toBeVisible();
      await expect(form.getByLabel("Day of week")).toBeVisible();
      await expect(form.getByLabel("Time zone")).toBeVisible();
      await form.scrollIntoViewIfNeeded();
      await form.screenshot({ path: testInfo.outputPath(`maintenance-schedule-${width}.png`) });
      await form.getByText("Advanced settings").click();
      await expect(form.getByLabel("Start delay maximum (seconds)")).toBeVisible();
      await form.screenshot({ path: testInfo.outputPath(`maintenance-advanced-${width}.png`) });
      await full.getByRole("checkbox", { name: "Run automatically" }).click();
      await expect(full.getByLabel("Frequency")).toBeVisible();
      await expect(full.getByText(/Full audits read all owned data/)).toBeVisible();
      await full.screenshot({ path: testInfo.outputPath(`maintenance-full-${width}.png`) });
      if (width === 390) {
        const quickAction = await page
          .getByRole("button", { name: "Run quick check" })
          .boundingBox();
        const fullAction = await page.getByRole("button", { name: "Run full check" }).boundingBox();
        expect(quickAction?.x).toBeCloseTo(fullAction!.x, 0);
        expect(quickAction?.width).toBeCloseTo(fullAction!.width, 0);
        const bounds = await page.evaluate(() => {
          const quick = document.querySelector<HTMLFormElement>("#audit-policy-quick")!;
          const full = document.querySelector<HTMLFormElement>("#audit-policy-full")!;
          const quickRect = quick.getBoundingClientRect();
          const fullRect = full.getBoundingClientRect();
          return {
            quickLeft: quickRect.left,
            fullLeft: fullRect.left,
            quickRight: quickRect.right,
            fullRight: fullRect.right,
            scrollWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
          };
        });
        expect(bounds.fullLeft).toBeCloseTo(bounds.quickLeft, 0);
        expect(bounds.fullRight).toBeCloseTo(bounds.quickRight, 0);
        expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.viewportWidth);
        await backupCard.scrollIntoViewIfNeeded();
        await backupCard.screenshot({ path: testInfo.outputPath("maintenance-backup-mobile.png") });
      }
    }
  });

  test("reviews similar model candidates at both viewport sizes", async ({ page }, testInfo) => {
    await page.route("**/api/v1/similarity/candidates**", (route) =>
      route.fulfill({ json: { items: [aSimilarityCandidate()], next_cursor: null } }),
    );
    await page.goto("/library/similar", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: "Start analysis" })).toBeVisible({
      timeout: 45_000,
    });
    await expect(page.getByRole("link", { name: "Compare" })).toBeVisible({ timeout: 45_000 });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page
        .getByRole("heading", { name: "Similar models", level: 1 })
        .scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`similar-${width}.png`), fullPage: true });
      await page.getByText("Analysis options", { exact: true }).click();
      await expect(
        page.getByRole("checkbox", { name: "Enable similarity analysis" }),
      ).toBeVisible();
      await page
        .getByRole("form", { name: "Similar models" })
        .getByText("Advanced settings")
        .click();
      await page.getByText("More filters", { exact: true }).click();
      await page.getByText("Analysis history", { exact: true }).click();
      await page
        .getByRole("heading", { name: "Find matches" })
        .locator('xpath=ancestor::*[contains(@class,"overflow-hidden")][1]')
        .screenshot({ path: testInfo.outputPath(`similar-analysis-${width}.png`) });
      await page.getByRole("link", { name: "Compare" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`similar-candidate-${width}.png`) });
      await page.getByText("Analysis history", { exact: true }).click();
      await page.getByText("More filters", { exact: true }).click();
      await page
        .getByRole("form", { name: "Similar models" })
        .getByText("Advanced settings")
        .click();
      await page.getByText("Analysis options", { exact: true }).click();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
      ).toBeLessThanOrEqual(1);
    }
  });

  test("guides AI setup across screen sizes", async ({ page }, testInfo) => {
    for (const theme of ["light", "dark"]) {
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        await openAiSearchSettings(page);
        await expect(
          page.getByRole("heading", { name: "Where should AI Search run?" }),
        ).toBeVisible({ timeout: 45_000 });
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
        await page.getByRole("tab", { name: "Technical" }).click();
        await expect(
          page.getByRole("checkbox", { name: "Enable AI Search", exact: true }),
        ).toBeVisible();
        await page.getByRole("tab", { name: "Guided setup" }).click();
        await expect(
          page.getByRole("heading", { name: "Where should AI Search run?" }),
        ).toBeVisible();
      }
    }
  });

  test("shows AI Search setup choices without nested menus", async ({ page }, testInfo) => {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await openAiSearchSettings(page);
      await expect(page.getByRole("tab", { name: "Search types" })).toBeVisible({
        timeout: 45_000,
      });
      await page.getByRole("tab", { name: "Search types" }).click();
      await expect(page.getByRole("tab", { name: "Technical" })).toBeInViewport();
      await expect(page.getByRole("heading", { name: "Search currently in use" })).toBeVisible({
        timeout: 45_000,
      });
      await expect(page.getByText("Loading...", { exact: true })).toHaveCount(0, {
        timeout: 45_000,
      });
      await expect(page.getByRole("radio", { name: /Text search/ })).toBeVisible();
      await expect(page.getByRole("radio", { name: /Visual search/ }).first()).toBeVisible();
      await expect(page.locator("details")).toHaveCount(0);
      await page.screenshot({
        path: testInfo.outputPath(`ai-search-types-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
      await page.getByText("AI model or server", { exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({
        path: testInfo.outputPath(`ai-models-${width}.png`),
        animations: "disabled",
      });
      await page.getByRole("button", { name: "Customize index" }).click();
      await page.getByText("Advanced index settings", { exact: true }).scrollIntoViewIfNeeded();
      await expect(page.getByText("Advanced index settings", { exact: true })).toBeInViewport();
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      await page.screenshot({
        path: testInfo.outputPath(`ai-index-options-${width}.png`),
        animations: "disabled",
      });
      await page.getByRole("button", { name: "Back to search choices" }).click();
      await expect(page.getByRole("button", { name: "Build new index" })).toBeVisible();
      await page.getByRole("tab", { name: "AI servers" }).click();
      await expect(page.getByRole("heading", { name: "Connect an AI server" })).toBeVisible();
      await page.getByRole("button", { name: "Add a server" }).click();
      await expect(page.getByRole("form", { name: "Inference server" })).toBeVisible();
      await page.getByRole("textbox", { name: "Server URL" }).scrollIntoViewIfNeeded();
      await page.screenshot({
        path: testInfo.outputPath(`ai-servers-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
      await page.getByRole("tab", { name: "Technical" }).click();
      await expect(page.getByLabel("Keyword ranking weight")).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
      ).toBeLessThanOrEqual(1);
    }
  });

  test("returns from technical options to guided setup", async ({ page }, testInfo) => {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await openAiSearchSettings(page);
      await expect(page.getByRole("tab", { name: "Technical" })).toBeVisible({ timeout: 45_000 });
      await page.getByRole("tab", { name: "Technical" }).click();
      await page
        .getByRole("checkbox", { name: "Enable AI Search", exact: true })
        .scrollIntoViewIfNeeded();
      const back = page.getByRole("tab", { name: "Guided setup" });
      await expect(back).toBeInViewport();
      await page.screenshot({
        path: testInfo.outputPath(`advanced-${width}.png`),
        animations: "disabled",
      });
      await back.click();
      await expect(
        page.getByRole("heading", { name: "Where should AI Search run?" }),
      ).toBeInViewport();
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
    const metadata = await backupFromAccepted(page, created);
    await expect(page.getByText(/Backup created/)).toBeVisible();

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
    const metadata = await createBackupViaApi(page);
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
    await page.getByRole("button", { name: "Backups + libraries" }).click();
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

  test("keeps remote storage stable while providers load", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    let releaseProviders: () => void = () => {};
    const pending = new Promise<void>((resolve) => {
      releaseProviders = resolve;
    });
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/api/v1/storage/providers", async (route) => {
      const response = await route.fetch();
      await pending;
      await route.fulfill({ response });
    });
    await page.goto("/settings?section=remote-storage", { waitUntil: "domcontentloaded" });
    const remote = page.getByRole("region", { name: "Remote storage" });

    await expect(remote.getByRole("status", { name: "Add remote connection" })).toBeVisible();
    await expect(remote.getByLabel("Connection name")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("remote-loading-1920.png") });
    releaseProviders();
    await expect(remote.getByRole("group", { name: "Storage category" })).toBeVisible();
    await page.unroute("**/api/v1/storage/providers");
  });

  test("guides remote provider selection responsively", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/settings?section=remote-storage");
    const remote = page.getByRole("region", { name: "Remote storage" });
    await expect(remote.getByRole("group", { name: "Storage category" })).toBeVisible();
    await remote
      .getByRole("group", { name: "Storage category" })
      .getByRole("button", { name: "Nextcloud and WebDAV" })
      .click();
    await expect(remote.getByLabel("Server URL")).toBeVisible();
    await remote
      .getByRole("group", { name: "Use for" })
      .getByRole("button", { name: "Backup replicas" })
      .click();
    await expect(
      remote
        .getByRole("group", { name: "Use for" })
        .getByRole("button", { name: "Backup replicas" }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.screenshot({
      path: testInfo.outputPath("remote-provider-1920.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(remote.getByLabel("Server URL")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await remote.getByRole("group", { name: "Provider" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("remote-provider-390.png") });
    await remote.getByLabel("Server URL").scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("remote-fields-390.png") });
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
    const metadata = await backupFromAccepted(page, await created);
    expect(metadata.source_ref).toBeTruthy();
    await expect(page.getByText(/Backup created/)).toBeVisible();

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
    const policyStatuses: number[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/maintenance/audit-policies"))
        policyStatuses.push(response.status());
    });
    // The exact navigation path carried by storage notification payloads.
    await page.goto("/settings?section=maintenance");
    const form = page.getByRole("form", { name: "Quick check schedule" });
    const enabled = form.getByRole("checkbox", { name: "Run automatically" });
    await expect(form).toBeVisible({ timeout: 45_000 });
    if ((await enabled.getAttribute("aria-checked")) !== "true") await enabled.click();
    await form.getByLabel("Frequency").selectOption("monthly");
    await form.getByText("Advanced settings").click();
    await form.getByLabel("Issue notification threshold").selectOption("critical");
    await form.getByLabel("Overdue after (minutes)").fill("45");
    const paused = form.getByRole("checkbox", { name: "Paused" });
    if ((await paused.getAttribute("aria-checked")) !== "true") await paused.click();
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/maintenance/audit-policies/quick") &&
          response.request().method() === "PUT" &&
          response.ok(),
      ),
      form.getByRole("button", { name: "Save changes" }).click(),
    ]);
    await page.reload();
    await expect(form.or(page.getByText("Could not load audit schedules."))).toBeVisible({
      timeout: 45_000,
    });
    expect(policyStatuses.at(-1), "audit policy API response after reload").toBe(200);
    await form.getByText("Advanced settings").click();
    await expect(form.getByRole("checkbox", { name: "Run automatically" })).toHaveAttribute(
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
      form.getByRole("button", { name: "Save changes" }).click(),
    ]);
  });

  test("requires explicit cleanup from storage insights", async ({ page }) => {
    const staged = await seedExpiredStaging();
    try {
      await page.goto("/settings?section=storage");
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
      await page.getByText("Measurement details").click();
      await expect(
        page.getByText(/Provider measurement:.*Capacity evidence is known/),
      ).toBeVisible();
      expect(existsSync(staged.path)).toBe(true);
      await page.getByRole("button", { name: "Clean up expired staging", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText("Uncertain files are retained");
      // Opening the confirmation must not remove bytes.
      expect(existsSync(staged.path)).toBe(true);
      await dialog.getByRole("button", { name: "Clean up", exact: true }).click();
      await expect(
        page.getByRole("status").filter({ hasText: "expired leases cleared" }),
      ).toBeVisible();
      await expect.poll(() => existsSync(staged.path)).toBe(false);
    } finally {
      const dismissed = await page.request.delete(`/api/v1/inbox/${staged.itemId}`);
      expect(dismissed.ok()).toBe(true);
    }
  });
});
