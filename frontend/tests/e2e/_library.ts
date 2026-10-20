import { expect, type Locator, type Page } from "@playwright/test";

/** Follow the visible recovery once; repeated conflicts still fail the caller. */
export async function refreshLibrary(page: Page): Promise<void> {
  const main = page.getByRole("main");
  const refresh = main.getByRole("button", { name: "Refresh library", exact: true });
  const acknowledged = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/v1/models/browse" &&
      !url.searchParams.has("cursor") &&
      response.status() === 200
    );
  });
  await refresh.click();
  await acknowledged;
  await expect(refresh).toBeHidden();
  await expect(main).toHaveAttribute("aria-busy", "false");
}

/** Establish content before testing a visit's stable ordering or navigation. */
export async function settleLibraryRead(page: Page, content: Locator): Promise<void> {
  const refresh = page.getByRole("main").getByRole("button", {
    name: "Refresh library",
    exact: true,
  });
  await expect(content.or(refresh).first()).toBeVisible();
  if (await refresh.isVisible()) await refreshLibrary(page);
  await expect(content).toBeVisible();
}
