/** Explicit pinned-model browser study; provision PLAYWRIGHT_AI_SEARCH_MODEL_DIR first. */
import { test, expect } from "../helpers";
import type { InferenceModel, SearchSettingsRead } from "../../../src/types/search";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("AI Search", () => {
  test("controls local sparse expansion through the browser", async ({ page }, testInfo) => {
    const initial: SearchSettingsRead = await (
      await page.request.get(`${API}/api/v1/config/ai-search`)
    ).json();
    const catalog: InferenceModel[] = await (
      await page.request.get(`${API}/api/v1/inference/models`)
    ).json();
    const sparse = catalog.find(
      (model) => model.key === "splade-pp-en-v1" && model.installed && model.curated,
    );
    expect(sparse).toBeDefined();
    const name = `Bicycle bracket ${Date.now()}`;
    const created = await page.request.post(`${API}/api/v1/documents`, {
      data: { name, body: "A bracket for attaching a bicycle light to the handlebar." },
    });
    expect(created.status()).toBe(201);
    const documentId = (await created.json()).id;
    try {
      await page.goto("/settings?section=ai-search");
      await page.getByRole("button", { name: "Advanced AI controls" }).click();
      const form = page.getByRole("form", { name: "AI Search", exact: true });
      await form.getByRole("checkbox", { name: "Enable AI Search", exact: true }).check();
      await form.getByRole("checkbox", { name: "Run AI on this machine", exact: true }).check();
      await form.getByText("Advanced settings", { exact: true }).click();
      await form.getByRole("combobox", { name: "Expansion model" }).selectOption(sparse!.id);
      await form.getByRole("checkbox", { name: "Enable lexical expansion" }).check();
      await form.getByRole("button", { name: "Save search settings" }).click();
      await expect(page.getByText("AI Search settings saved", { exact: true })).toBeVisible();
      await expect(page.getByText("AI Search settings saved", { exact: true })).not.toBeVisible();
      for (const [label, width, height] of [
        ["desktop", 1280, 900],
        ["mobile", 390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        const details = form.locator("details");
        if (!(await details.evaluate((element) => element.hasAttribute("open"))))
          await form.getByText("Advanced settings", { exact: true }).click();
        const field = form.getByRole("group", { name: "Lexical expansion (SPLADE)" });
        await field.evaluate((element) => element.scrollIntoView({ block: "center" }));
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await field.screenshot({ path: testInfo.outputPath(`sparse-${label}.png`) });
      }
      await expect
        .poll(
          async () => {
            const response = await page.request.get(`${API}/api/v1/search`, {
              params: { q: "cycling", mode: "lexical" },
            });
            return (await response.json()).items.some(
              (item: { subject_id: number; subject_type: string }) =>
                item.subject_id === documentId && item.subject_type === "document",
            );
          },
          { timeout: 60000 },
        )
        .toBe(true);
      await page.goto("/search?q=cycling");
      await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
      await page.goto("/settings?section=ai-search");
      await page.getByRole("button", { name: "Advanced AI controls" }).click();
      await form.getByText("Advanced settings", { exact: true }).click();
      await form.getByRole("checkbox", { name: "Enable lexical expansion" }).uncheck();
      await form.getByRole("button", { name: "Save search settings" }).click();
      await expect(page.getByText("AI Search settings saved", { exact: true })).toBeVisible();
      await page.goto("/search?q=cycling");
      await expect(page.getByRole("link", { name, exact: true })).toHaveCount(0);
      await page.goto("/search?q=bicycle");
      await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
    } finally {
      await page.request.put(`${API}/api/v1/config/ai-search`, { data: initial.settings });
      await page.request.delete(`${API}/api/v1/documents/${documentId}`);
    }
  });
});
