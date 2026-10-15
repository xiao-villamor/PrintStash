/** Guided AI setup shows each preparation step while a library is indexed. */
import { expect, test, type Page } from "@playwright/test";
import {
  anInferenceModel,
  aSearchGeneration,
  searchConfiguration,
  searchSettings,
  searchStatus,
} from "../../src/test-support/search";
import type { SearchGeneration } from "../../src/types/search";
import { useMockApi } from "./_setup";

useMockApi();

async function serveSearch(page: Page, generation: SearchGeneration) {
  await page.route("**/api/v1/config/ai-search", (route) =>
    route.fulfill({
      json: searchConfiguration({
        settings: searchSettings({ enabled: true, local_models_enabled: true }),
      }),
    }),
  );
  await page.route("**/api/v1/inference/models", (route) =>
    route.fulfill({ json: [anInferenceModel()] }),
  );
  await page.route("**/api/v1/config/ai-search/generations", (route) =>
    route.fulfill({ json: [generation] }),
  );
  await page.route("**/api/v1/search/status", (route) =>
    route.fulfill({ json: searchStatus({ enabled: true, semantic_ready: false }) }),
  );
}

test.describe("AI Search preparation", () => {
  test("shows each step while passages are indexed", async ({ page }, testInfo) => {
    await serveSearch(
      page,
      aSearchGeneration({
        state: "building",
        phase: "backfill",
        model: "bge-small-en-v1.5",
        eligible: 567,
        indexed: 212,
        verified_at: null,
      }),
    );
    for (const [label, width, height] of [
      ["desktop", 1280, 900],
      ["mobile", 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await page.goto("/settings?section=ai-search");
      const steps = page.getByRole("list", { name: "Preparation steps" });
      await expect(steps.getByRole("listitem")).toHaveText([
        /Check library content\(done\)/,
        /Index passages\(in progress\)/,
        /Catch up on recent changes\(not started\)/,
        /Verify the index\(not started\)/,
      ]);
      await expect(
        page.getByText(/bge-small-en-v1\.5 turns each passage into a vector/),
      ).toBeVisible();
      await expect(page.getByText("212 of 567 searchable entries prepared")).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await steps.screenshot({ path: testInfo.outputPath(`steps-${label}.png`) });
    }
  });
});
