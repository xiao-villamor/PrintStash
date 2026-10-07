/** Administrative drafts keep their conditional base across other editors. */
import { test, expect } from "./helpers";

test.describe("Maintenance editing", () => {
  test("reviews a schedule conflict in the browser", async ({ page, context }) => {
    await page.goto("/settings?section=maintenance");
    const form = page.getByRole("form", { name: "Quick check schedule" });
    await form.getByRole("checkbox", { name: "Run automatically" }).click();
    await form.getByRole("button", { name: "Save changes" }).click();
    await expect(form.getByRole("button", { name: "Save changes" })).toHaveCount(0);
    await form.getByLabel("Time zone", { exact: true }).fill("Europe/Madrid");
    const other = await context.newPage();
    try {
      await other.goto("/settings?section=maintenance");
      const otherForm = other.getByRole("form", { name: "Quick check schedule" });
      await otherForm.getByLabel("Time zone", { exact: true }).fill("Europe/Paris");
      await otherForm.getByRole("button", { name: "Save changes" }).click();
      await expect(otherForm.getByRole("button", { name: "Save changes" })).toHaveCount(0);
      await form.getByRole("button", { name: "Save changes" }).click();
      await expect(form.getByLabel("Time zone", { exact: true })).toHaveValue("Europe/Madrid");
      await form.getByRole("button", { name: "Review current values" }).click();
      await form.getByRole("button", { name: "Use current values" }).click();
      await expect(form.getByLabel("Time zone", { exact: true })).toHaveValue("Europe/Paris");
      await form.getByLabel("Time zone", { exact: true }).fill("UTC");
      await form.getByRole("checkbox", { name: "Run automatically" }).click();
      await form.getByRole("button", { name: "Save changes" }).click();
      await expect(form.getByRole("button", { name: "Save changes" })).toHaveCount(0);
    } finally {
      await other.close();
    }
  });
});
