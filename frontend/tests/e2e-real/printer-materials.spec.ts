/** Manual printer state keeps a rejected draft until the operator adopts current values. */
import { test, expect } from "./helpers";

test.describe("Manual printer materials", () => {
  test("reviews a manual material conflict in the browser", async ({ page, context }) => {
    const created = await page.request.post("/api/v1/printers", {
      data: {
        name: `e2e-materials-${Date.now()}`,
        provider: "moonraker",
        moonraker_url: "http://127.0.0.1:1",
      },
    });
    expect(created.ok()).toBe(true);
    const printer = await created.json();
    const other = await context.newPage();
    try {
      await page.goto(`/printers/${printer.id}`);
      await page.getByRole("tab", { name: "Materials & tools" }).click();
      const nozzle = page.getByRole("spinbutton", { name: "Tool 0 nozzle diameter (mm)" });
      await nozzle.fill("0.6");
      await other.goto(`/printers/${printer.id}`);
      await other.getByRole("tab", { name: "Materials & tools" }).click();
      await other.getByRole("spinbutton", { name: "Tool 0 nozzle diameter (mm)" }).fill("0.8");
      await other.getByRole("button", { name: "Save state" }).click();
      await expect(other.getByText("Materials and tools saved")).toBeVisible();
      await page.getByRole("button", { name: "Save state" }).click();
      await expect(nozzle).toHaveValue("0.6");
      await page.getByRole("button", { name: "Review current values" }).click();
      await page.getByRole("button", { name: "Use current values" }).click();
      await expect(nozzle).toHaveValue("0.8");
    } finally {
      await other.close();
      await page.request.delete(`/api/v1/printers/${printer.id}`);
    }
  });
});
