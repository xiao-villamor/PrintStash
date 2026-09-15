/** Administrator cache controls persist policy without confusing clear with disable. */
import { test, expect } from "./helpers";

test.describe("remote Artifact cache", () => {
  test("manages cache policy through Settings", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Storage", exact: true }).click();
    await page.getByText("Advanced cache settings", { exact: true }).click();
    await page.getByRole("spinbutton", { name: "Maximum cached files" }).fill("125");
    await page.getByRole("button", { name: "Save cache settings" }).click();
    await expect(page.getByRole("button", { name: "Save cache settings" })).toBeEnabled();
    await page.reload();
    await page.getByRole("button", { name: "Storage", exact: true }).click();
    await page.getByText("Advanced cache settings", { exact: true }).click();
    await expect(page.getByRole("spinbutton", { name: "Maximum cached files" })).toHaveValue("125");
    await expect(page.getByRole("button", { name: "Clear cached files" })).toHaveCount(0);
    await page.getByRole("button", { name: "Restore cache defaults" }).click();
    await expect(page.getByRole("spinbutton", { name: "Maximum cached files" })).toHaveValue(
      "10000",
    );
  });
});
