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
import { collapseNavigationSidebar } from "../e2e-real/util";

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
    route.fulfill({
      json: searchStatus({ enabled: true, semantic_ready: generation.state === "active" }),
    }),
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
    await page.goto("/settings?section=ai-search");
    await collapseNavigationSidebar(page);
    await expect(page.getByText("212 of 567 searchable entries prepared")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("preparation.png") });

    const steps = page.getByRole("list", { name: "Preparation steps" });
    await expect(steps).toBeVisible();
    await expect(steps.getByRole("listitem")).toHaveText([
      /Check library content\(done\)/,
      /Index passages\(in progress\)/,
      /Catch up on recent changes\(not started\)/,
      /Verify the index\(not started\)/,
    ]);
    await expect(steps.locator('[aria-current="step"]')).toHaveText(/Index passages/);
    await expect(
      page.getByText(/bge-small-en-v1\.5 turns each passage into a vector/),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
  test("aligns the ready status with its actions", async ({ page }, testInfo) => {
    await serveSearch(page, aSearchGeneration({ state: "active" }));

    await page.goto("/settings?section=ai-search");
    await collapseNavigationSidebar(page);

    const ready = page.getByText("Ready", { exact: true });
    const trySearch = page.getByRole("link", { name: "Try AI Search" });
    await expect(ready).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("ready.png") });
    await expect(ready).toHaveCSS("border-radius", "9999px", { timeout: 1000 });
    const badge = (await ready.boundingBox())!;
    const action = (await trySearch.boundingBox())!;
    expect(badge.y + badge.height / 2).toBeCloseTo(action.y + action.height / 2, 0);
    const group = page.getByRole("group", { name: "AI Search is ready" });
    const heading = group.getByRole("heading", { name: "AI Search is ready" });
    const panel = group.getByRole("region", { name: "Index check" });
    await expect(panel).toBeVisible({ timeout: 1000 });
    const title = (await heading.boundingBox())!;
    const rows = (await panel.boundingBox())!;
    expect(title.y + title.height).toBeLessThan(rows.y);
  });

  test("shows both processing locations in the setup form", async ({ page }, testInfo) => {
    await serveSearch(page, aSearchGeneration({ state: "active" }));

    await page.goto("/settings?section=ai-search");
    await collapseNavigationSidebar(page);
    await page.getByRole("button", { name: "Change setup" }).click();
    const dialog = page.getByRole("dialog", { name: "AI Search setup" });
    await expect(dialog.getByRole("button", { name: "Prepare my library" })).toBeVisible();
    await dialog.screenshot({ path: testInfo.outputPath("setup.png") });

    await expect(dialog.getByRole("radio", { name: "On this machine" })).toBeChecked({
      timeout: 1000,
    });
    const remote = dialog.getByRole("radio", { name: "Another server" });
    await remote.focus();
    await remote.press("Space");
    await expect(dialog.getByRole("form", { name: "Inference server" })).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: "Change setup" })).toBeFocused();
  });
});
