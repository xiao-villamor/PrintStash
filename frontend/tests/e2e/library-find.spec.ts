/** Ctrl+F in a collection opens an editor-style find bar that walks the matching models. */
import { expect, test } from "@playwright/test";
import { aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/collections/children?**", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/api/v1/models/browse?**", (route) =>
    route.fulfill({
      json: {
        items: [
          aModelListItem({ id: 1, name: "Alpha bracket" }),
          aModelListItem({ id: 2, name: "Beta mount" }),
          aModelListItem({ id: 3, name: "Gamma bracket" }),
        ].map((model) => ({ kind: "model", model })),
        total: 3,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    }),
  );
});

test("finds models in the collection with Ctrl+F", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("main").getByRole("link", { name: /Alpha bracket/ })).toBeVisible();

  await page.keyboard.press("Control+f");
  const find = page.getByRole("search", { name: "Find in collection" });
  await expect(find).toBeVisible();
  await find.getByRole("textbox").fill("bracket");

  await expect(find.getByRole("status")).toHaveText("1 of 2");
  await page.keyboard.press("Enter");
  await expect(find.getByRole("status")).toHaveText("2 of 2");
  await page.keyboard.press("Shift+Enter");
  await expect(find.getByRole("status")).toHaveText("1 of 2");

  await find.getByRole("textbox").fill("zzz");
  await expect(find.getByRole("status")).toHaveText("No results");

  await page.keyboard.press("Escape");
  await expect(find).toBeHidden();
});
