/** Guided AI setup offers every curated local text model, the recommended one first. */
import { expect, test } from "@playwright/test";
import {
  anInferenceModel,
  searchConfiguration,
  searchSettings,
  searchStatus,
} from "../../src/test-support/search";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("AI Search local models", () => {
  test("offers the smaller English model beside the recommended one", async ({
    page,
  }, testInfo) => {
    await page.route("**/api/v1/config/ai-search", (route) =>
      route.fulfill({
        json: searchConfiguration({
          settings: searchSettings({ enabled: true, local_models_enabled: true }),
        }),
      }),
    );
    await page.route("**/api/v1/inference/models", (route) =>
      route.fulfill({
        json: [
          anInferenceModel({
            id: "b".repeat(64),
            key: "all-minilm-l6-v2",
            installed: false,
            recommended: false,
            size_bytes: 90880310,
          }),
          anInferenceModel({ installed: false }),
        ],
      }),
    );
    await page.route("**/api/v1/config/ai-search/generations", (route) =>
      route.fulfill({ json: [] }),
    );
    await page.route("**/api/v1/search/status", (route) =>
      route.fulfill({ json: searchStatus({ enabled: true, semantic_ready: false }) }),
    );
    for (const [label, width, height] of [
      ["desktop", 1280, 900],
      ["mobile", 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await page.goto("/settings?section=ai-search");
      const recommended = page.getByRole("radio", { name: /bge-small-en-v1\.5 .* Recommended/ });
      const smaller = page.getByRole("radio", { name: /all-minilm-l6-v2 [\d.]+ MB · en$/ });
      await expect(recommended).toBeChecked();
      await expect(smaller).not.toBeChecked();
      await smaller.check();
      await expect(page.getByText(/Download size: 86\.7 MB/)).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await page
        .getByRole("group", { name: "Choose a different AI model" })
        .screenshot({ path: testInfo.outputPath(`models-${label}.png`) });
    }
  });
});
