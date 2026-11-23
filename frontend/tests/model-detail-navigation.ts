import { expect, type Page } from "@playwright/test";

export async function selectModelDetailTab(page: Page, name: string | RegExp): Promise<void> {
  const inspector = page.getByTestId("model-detail-sidebar");
  const tab = inspector.getByRole("tab", { name, exact: true });
  const more = inspector.getByRole("button", { name: "More tabs", exact: true });
  await expect(tab.or(more).first()).toBeVisible();
  if (await tab.isVisible()) {
    await tab.click();
    return;
  }
  await more.click();
  await page.getByRole("menuitemradio", { name, exact: true }).click();
}
