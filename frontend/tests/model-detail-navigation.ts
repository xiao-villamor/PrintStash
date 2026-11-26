import { expect, type Page } from "@playwright/test";

export async function selectModelDetailTab(page: Page, name: string | RegExp): Promise<void> {
  const inspector = page.getByTestId("model-detail-sidebar");
  if (name === "Print settings" || name === "Cost estimate") {
    const print = inspector.getByRole("tab", { name: /^Print\b/ });
    if ((await print.getAttribute("aria-selected")) !== "true") await print.click();
    await inspector.getByRole("button", { name, exact: true }).click();
    return;
  }
  if (name === "Similar") {
    await page.getByRole("button", { name: "More model actions" }).click();
    await page.getByRole("menuitem", { name: "Similar models", exact: true }).click();
    return;
  }
  if (name === "Source and license") {
    await inspector.getByRole("tab", { name: "Overview", exact: true }).click();
    await inspector.getByRole("link", { name: "Source details", exact: true }).click();
    return;
  }
  const tab = inspector.getByRole("tab", { name, exact: true });
  await expect(tab).toBeVisible();
  await tab.click();
}
