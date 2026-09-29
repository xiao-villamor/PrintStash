/** Long collection paths stay readable inside the upload dialog at narrow widths. */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";

useMockApi();

const nestedPath = "printstash-data/stackable-vertical-garden-planter-with-extra-parts";
const nestedDisplayPath = "PrintStash Data/Stackable Vertical Garden Planter With Extra Parts";

test.describe("Upload collection selector", () => {
  test("keeps a nested collection path inside the upload selector", async ({ page }) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width: 625, height: 844 });
    // The destination picker searches; it never lists the whole library (#295).
    const folder = {
      model_count: 0,
      effective_role: "admin",
      tags: [],
      has_readme: false,
      child_count: 0,
      descendant_count: 0,
    };
    await page.route("**/api/v1/collections/search**", (route) =>
      route.fulfill({
        json: {
          items: [
            {
              ...folder,
              id: 1,
              name: "PrintStash Data",
              slug: "printstash-data",
              path: "printstash-data",
              parent_id: null,
              display_path: "PrintStash Data",
              child_count: 1,
              descendant_count: 1,
            },
            {
              ...folder,
              id: 2,
              name: "Stackable Vertical Garden Planter With Extra Parts",
              slug: "stackable-vertical-garden-planter-with-extra-parts",
              path: nestedPath,
              parent_id: 1,
              display_path: nestedDisplayPath,
            },
          ],
          next_cursor: null,
        },
      }),
    );

    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Upload model" });
    await dialog.getByRole("button", { name: "None" }).click();
    await page
      .getByRole("option", {
        name: new RegExp("Stackable Vertical Garden Planter With Extra Parts"),
      })
      .click();
    const selector = dialog.getByRole("button", { name: nestedDisplayPath });

    const bounds = await selector.evaluate((button) => {
      const label = button.querySelector("span")!;
      const buttonBox = button.getBoundingClientRect();
      const labelBox = label.getBoundingClientRect();
      return {
        buttonTop: buttonBox.top,
        buttonBottom: buttonBox.bottom,
        labelTop: labelBox.top,
        labelBottom: labelBox.bottom,
        lineHeight: Number.parseFloat(getComputedStyle(label).lineHeight),
        labelHeight: labelBox.height,
      };
    });
    expect(bounds.labelHeight).toBeLessThanOrEqual(bounds.lineHeight);
    expect(bounds.labelTop).toBeGreaterThanOrEqual(bounds.buttonTop);
    expect(bounds.labelBottom).toBeLessThanOrEqual(bounds.buttonBottom);
    await expect(selector).toHaveAttribute("title", nestedDisplayPath);
  });
});
