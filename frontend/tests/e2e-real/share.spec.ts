/**
 * Public share links, and what they do and do not allow.
 *
 * A share link is the only unauthenticated way into the library, so the two things that
 * matter are that view-only really cannot download, and that revoking one **breaks the
 * page immediately**. A revoked link that keeps working is the failure that cannot be
 * undone after the fact.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, uploadGcodeModel } from "./util";

async function openShareDialog(page: import("@playwright/test").Page, name: string) {
  await page.goto("/"); // may be coming back from a public /share page
  await modelCard(page, name).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await clickModelAction(page, "Share");
  await expect(page.getByRole("dialog")).toBeVisible();
}

// Read the freshly-minted link URL out of the dialog's read-only field.
async function createLinkAndGetUrl(page: import("@playwright/test").Page): Promise<string> {
  await page.getByRole("button", { name: "Create link" }).click();
  const tokenField = page.getByRole("dialog").locator("input[readonly]");
  await expect(tokenField).toBeVisible();
  return tokenField.inputValue();
}

test.describe("share links", () => {
  test("view-only vs downloadable public share links", async ({ page }) => {
    const name = `e2e-lnk-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await openShareDialog(page, name);

    // Default link is view-only.
    const viewOnlyUrl = await createLinkAndGetUrl(page);

    await page.goto(viewOnlyUrl);
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByText("Downloads are disabled for this link — view only.")).toBeVisible();
    await expect(page.getByRole("link", { name: /download/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "G-code" })).toBeDisabled();

    // Now a downloadable link.
    await openShareDialog(page, name);
    await page.getByText("Allow file download").click();
    const downloadUrl = await createLinkAndGetUrl(page);

    await page.goto(downloadUrl);
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByRole("link", { name: /download/i }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "G-code" })).toBeEnabled();
  });

  test("opens a working capability after a denied token without private credentials", async ({
    page,
  }) => {
    const name = `e2e-public-scope-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await openShareDialog(page, name);
    const url = await createLinkAndGetUrl(page);
    const publicRequests: Array<Promise<Record<string, string>>> = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/v1/share/")) publicRequests.push(request.allHeaders());
    });
    await page.goto("/share/e2e-unissued-token");
    await expect(page.getByText("This share link is invalid, expired, or revoked.")).toBeVisible();
    // Browser-history navigation stays in the mounted public route, exercising token replacement.
    await page.evaluate((target) => {
      window.history.pushState({}, "", target);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, new URL(url).pathname);
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByText("This share link is invalid, expired, or revoked.")).toHaveCount(0);
    await expect(page).toHaveTitle(new RegExp(name));
    await expect(page.getByRole("link", { name: /download/i })).toHaveCount(0);
    expect(publicRequests.length).toBeGreaterThanOrEqual(2);
    for (const headers of await Promise.all(publicRequests)) {
      expect(headers.cookie).toBeUndefined();
      expect(headers.authorization).toBeUndefined();
    }
  });

  test("revoking a share link breaks the public page", async ({ page }) => {
    const name = `e2e-rvk-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await openShareDialog(page, name);

    const url = await createLinkAndGetUrl(page);
    await page.goto(url);
    await expect(page.getByRole("heading", { name })).toBeVisible();

    // Reopen the dialog and revoke the (only) active link.
    await openShareDialog(page, name);
    await page.getByRole("button", { name: "Revoke" }).click();
    // Revoked links are removed from the list entirely.
    await expect(page.getByText("No share links yet.")).toBeVisible();

    // The token now 404s — the public page shows the error state.
    await page.goto(url);
    await expect(page.getByText("This share link is invalid, expired, or revoked.")).toBeVisible();
  });
});
