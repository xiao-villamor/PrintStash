/** Real AI settings freshness preserves local ranking drafts before explicit persistence. */
import { test, expect } from "./helpers";
import type { SearchSettingsRead } from "../../src/types/search";
const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
test.describe("AI Search settings freshness", () => {
  test("keeps a ranking draft through real settings freshness", async ({ page, context }) => {
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
            data: { ...initial.settings, lexical_weight: 3 },
          })
        ).ok(),
      ).toBe(true);
      const other = await context.newPage();
      await other.goto("about:blank");
      const refreshed = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/config/ai-search") &&
          response.request().method() === "GET",
      );
      await page.bringToFront();
      const fresh: SearchSettingsRead = await (await refreshed).json();
      expect(fresh.settings.lexical_weight).toBe(3);
      await expect(weight).toHaveValue("2");
      await other.close();
      await page.getByRole("button", { name: "Save search settings" }).click();
      await expect(page.getByText("AI Search settings saved", { exact: true })).toBeVisible();
      const saved: SearchSettingsRead = await (
        await page.request.get(`${API}/api/v1/config/ai-search`)
      ).json();
      expect(saved.settings.lexical_weight).toBe(2);
    } finally {
      await page.request.put(`${API}/api/v1/config/ai-search`, { data: initial.settings });
    }
  });
});
