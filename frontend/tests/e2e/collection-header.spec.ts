/** Collection edits must work against the shared preview API without response overrides. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Collection header preview", () => {
  test("persists consecutive inline tags through the preview API", async ({ page }) => {
    await page.goto("/?c=maraio");
    const opener = page.getByRole("button", { name: "+ Add tag", exact: true });
    await expect(opener).toBeVisible();
    const idleHeight = (await opener.boundingBox())!.height;
    await opener.click();
    const input = page.getByRole("textbox", { name: "New tag" });
    const field = input.locator("..");
    await expect(input).toHaveCSS("box-shadow", "none");
    await expect(field).toHaveCSS("box-shadow", "none");
    await expect(field).toHaveCSS("border-style", "dashed");
    await expect(field).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    expect((await field.boundingBox())!.height).toBe(idleHeight);
    await input.fill("#Outdoor Parts");
    const first = page.waitForResponse(
      (r) => r.url().endsWith("/collections/1/tags") && r.request().method() === "PUT",
    );
    await input.press("Enter");
    expect((await first).status()).toBe(200);
    await expect(input).toHaveValue("");
    await expect(input).toBeFocused();
    await expect(page.getByRole("button", { name: "Remove tag outdoor-parts" })).toBeVisible();
    await input.fill("functional");
    await input.press(",");
    await expect(page.getByRole("button", { name: "Remove tag functional" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Remove tag outdoor-parts" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove tag functional" })).toBeVisible();
  });

  test("persists tag removal through the preview API", async ({ page }) => {
    const seed = await page.request.put("/api/v1/collections/1/tags", {
      data: { tags: ["functional", "outdoor"] },
    });
    expect(seed.status()).toBe(200);
    await page.goto("/?c=maraio");
    await page.getByRole("button", { name: "Remove tag outdoor" }).click();
    await expect(page.getByRole("button", { name: "Remove tag outdoor" })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("button", { name: "Remove tag functional" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove tag outdoor" })).toHaveCount(0);
  });

  test("keeps a duplicate draft available for correction", async ({ page }) => {
    await page.request.put("/api/v1/collections/1/tags", { data: { tags: ["functional"] } });
    await page.goto("/?c=maraio");
    await page.getByRole("button", { name: "+ Add tag", exact: true }).click();
    const input = page.getByRole("textbox", { name: "New tag" });
    await input.fill("FUNCTIONAL");
    await input.press("Enter");
    await expect(page.getByRole("alert")).toHaveText("#functional is already on this collection.");
    await expect(input).toHaveValue("FUNCTIONAL");
    await expect(input).toBeFocused();
    await input.fill("outdoor");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await input.press("Enter");
    await expect(page.getByRole("button", { name: "Remove tag outdoor" })).toBeVisible();
  });

  test("keeps the empty description aligned with collection information", async ({ page }) => {
    await page.goto("/?c=maraio");
    const header = page.locator(".library-collection-header");
    const description = header.getByRole("button", {
      name: "Add a description for this collection",
      exact: true,
    });
    await expect(description).toBeVisible();
    await expect(description.locator("svg")).toHaveCount(0);
    await expect(description).toHaveCSS("font-size", "13px");
    const bounds = await description.boundingBox();
    const meta = await header.locator(".library-collection-meta").boundingBox();
    expect(bounds!.height).toBeLessThan(20);
    expect(Math.abs(bounds!.x - meta!.x)).toBeLessThan(1);
    expect(meta!.y - bounds!.y - bounds!.height).toBeCloseTo(8, 0);
  });

  test("persists collection description through the preview API", async ({ page }) => {
    await page.goto("/?c=maraio");
    await page
      .getByRole("button", { name: "Add a description for this collection", exact: true })
      .click();
    const description =
      "Base in PETG for UV resistance. Legs are fine in PLA. Use a brim on the large pieces.";
    const input = page.getByRole("textbox", { name: "Description", exact: true });
    await input.fill(description);
    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/collections/1/readme") && r.request().method() === "PUT",
    );
    await input.press("Control+Enter");
    expect((await saved).status()).toBe(200);
    await expect(page.getByText(description, { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText(description, { exact: true })).toBeVisible();
    const text = page.locator(".collection-header-readme .prose p");
    await expect(text).toHaveCSS("font-size", "13px");
    await expect(text).toHaveCSS("margin-top", "0px");
  });
});
