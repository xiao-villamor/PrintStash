/** Real AI settings freshness preserves local ranking drafts before explicit persistence. */
import { test, expect } from "./helpers";
import type { SearchSettingsRead } from "../../src/types/search";
const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
test.describe("AI Search settings freshness", () => {
  test("keeps a ranking draft through reconnect freshness", async ({ page, context }) => {
    const response = await page.request.get(`${API}/api/v1/config/ai-search`);
    expect(response.ok()).toBe(true);
    const initial: SearchSettingsRead = await response.json();
    try {
      expect(
        (
          await page.request.put(`${API}/api/v1/config/ai-search`, {
            data: { ...initial.settings, lexical_weight: 1 },
          })
        ).ok(),
      ).toBe(true);
      await page.goto("/settings?section=ai-search");
      await page.getByRole("tab", { name: "Technical" }).click();
      const weight = page.getByRole("spinbutton", { name: "Keyword ranking weight" });
      await weight.fill("2");
      expect(
        (
          await page.request.put(`${API}/api/v1/config/ai-search`, {
            data: { ...initial.settings, lexical_weight: 3, semantic_weight: 4 },
          })
        ).ok(),
      ).toBe(true);
      // Headless Chromium can keep both pages visible, so bringToFront does not
      // reliably produce the visibility transition Query listens to. Reconnect
      // exercises a real browser freshness trigger without a synthetic event.
      await context.setOffline(true);
      const refreshed = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/config/ai-search") &&
          response.request().method() === "GET",
        { timeout: 10_000 },
      );
      await context.setOffline(false);
      const fresh: SearchSettingsRead = await (await refreshed).json();
      expect(fresh.settings.lexical_weight).toBe(3);
      await expect(weight).toHaveValue("2");
      const conflict = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/config/ai-search") &&
          response.request().method() === "PUT",
      );
      await page.getByRole("button", { name: "Save search settings" }).click();
      expect((await conflict).status()).toBe(412);
      await expect(weight).toHaveValue("2");
      await page.getByRole("button", { name: "Review latest version" }).click();
      await page.getByRole("button", { name: "Save my draft against this version" }).click();
      await expect(page.getByText("AI Search settings saved", { exact: true })).toBeVisible();
      const saved: SearchSettingsRead = await (
        await page.request.get(`${API}/api/v1/config/ai-search`)
      ).json();
      expect(saved.settings.lexical_weight).toBe(2);
      expect(saved.settings.semantic_weight).toBe(4);
    } finally {
      await context.setOffline(false);
      await page.request.put(`${API}/api/v1/config/ai-search`, { data: initial.settings });
    }
  });
  test("resolves competing browser edits through explicit review", async ({ page, context }) => {
    const initial: SearchSettingsRead = await (
      await page.request.get(`${API}/api/v1/config/ai-search`)
    ).json();
    const second = await context.newPage();
    try {
      expect(
        (
          await page.request.put(`${API}/api/v1/config/ai-search`, {
            data: { ...initial.settings, lexical_weight: 1, semantic_weight: 1 },
          })
        ).ok(),
      ).toBe(true);
      await page.goto("/settings?section=ai-search");
      await page.getByRole("tab", { name: "Technical" }).click();
      await second.goto("/settings?section=ai-search");
      await second.getByRole("tab", { name: "Technical" }).click();
      await page.getByRole("spinbutton", { name: "Keyword ranking weight" }).fill("2");
      await second.getByRole("spinbutton", { name: "Semantic ranking weight" }).fill("3");
      await page.getByRole("button", { name: "Save search settings" }).click();
      await expect(page.getByText("AI Search settings saved", { exact: true })).toBeVisible();
      await second.getByRole("button", { name: "Save search settings" }).click();
      await expect(second.getByRole("button", { name: "Review latest version" })).toBeVisible();
      await expect(second.getByRole("spinbutton", { name: "Semantic ranking weight" })).toHaveValue(
        "3",
      );
      await second.getByRole("button", { name: "Review latest version" }).click();
      await second.getByRole("button", { name: "Save my draft against this version" }).click();
      await expect(second.getByText("AI Search settings saved", { exact: true })).toBeVisible();
      const saved: SearchSettingsRead = await (
        await page.request.get(`${API}/api/v1/config/ai-search`)
      ).json();
      expect(saved.settings.lexical_weight).toBe(2);
      expect(saved.settings.semantic_weight).toBe(3);
    } finally {
      await second.close();
      await page.request.put(`${API}/api/v1/config/ai-search`, { data: initial.settings });
    }
  });
});
