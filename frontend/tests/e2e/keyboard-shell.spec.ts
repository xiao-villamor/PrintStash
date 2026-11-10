/** Reference keyboard commands retain text editing, modal ownership and safe Library returns. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();
test.describe("Shared keyboard commands", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  test("shows the authoritative two-column catalogue", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    await page.keyboard.press("Shift+?");
    const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
    for (const label of ["Find and move around", "Models", "Print results"])
      await expect(help.getByRole("heading", { name: label, exact: true })).toBeVisible();
    for (const label of [
      "Search panel",
      "Collections panel",
      "Show or hide the sidebar",
      "Show or hide details",
      "Move in the collection tree",
      "Close details, then clear the selection",
      "Rename this collection",
      "Undo the last change",
      "Show this list",
      "Choose a revision",
      "Log a print that came out OK",
      "Log a failed print, with a note",
      "Save the failure note",
    ])
      await expect(help.getByText(label, { exact: true })).toBeVisible();
    const boxes = await help.locator("section").evaluateAll((groups) =>
      groups.map((group) => ({
        x: group.getBoundingClientRect().x,
        y: group.getBoundingClientRect().y,
      })),
    );
    expect(boxes[0].x).toBeLessThan(boxes[1].x);
    expect(boxes[2].x).toBe(boxes[0].x);
    expect(boxes[2].y).toBeGreaterThan(boxes[0].y);
    await page.keyboard.press("Shift+?");
    await expect(help).toBeHidden();
  });

  test("operates pane commands from focused text inputs", async ({ page }) => {
    await page.goto("/?c=maraio");
    await expect(page.getByRole("heading", { name: "maraio", exact: true })).toBeVisible();
    await page.keyboard.press("/");
    await expect(
      page.locator("header").getByRole("searchbox", { name: "Search library" }),
    ).toBeFocused();
    await page.keyboard.press("Control+Shift+F");
    await expect(
      page
        .getByRole("complementary", { name: "Search panel" })
        .getByRole("searchbox", { name: "Search library" }),
    ).toBeFocused();
    await page.keyboard.press("Control+b");
    const toggle = page.locator("header").getByRole("button", { name: "Toggle sidebar" });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toBeFocused();
    await page.keyboard.press("Control+b");
    await page.keyboard.press("Control+Shift+E");
    const collection = page
      .getByRole("complementary", { name: "Collections" })
      .getByRole("treeitem", { name: "maraio", exact: true });
    await expect(collection).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(collection).toHaveAttribute("aria-expanded", "true");
    await expect(
      page.getByRole("treeitem", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("ArrowDown");
    await expect(
      page.getByRole("treeitem", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(collection).toBeFocused();
    await page.keyboard.press("F2");
    await expect(
      page
        .getByRole("dialog", { name: "Rename maraio", exact: true })
        .getByRole("textbox", { name: "Collection name" }),
    ).toBeFocused();
  });

  test("preserves reserved keyboard ownership", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    await page.keyboard.press("/");
    const input = page.locator("header").getByRole("searchbox", { name: "Search library" });
    await input.evaluate((node) =>
      node.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "F",
          code: "KeyF",
          ctrlKey: true,
          shiftKey: true,
          isComposing: true,
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    await expect(page.getByRole("complementary", { name: "Search panel" })).toHaveCount(0);
    await input.blur();
    await page.keyboard.press("Shift+?");
    const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
    await expect(help).toBeVisible();
    await page.keyboard.press("Control+Shift+F");
    await expect(help).toBeFocused();
    await expect(page.getByRole("complementary", { name: "Search panel" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+Shift+F");
    await expect(
      page
        .getByRole("complementary", { name: "Search panel" })
        .getByRole("searchbox", { name: "Search library" }),
    ).toBeFocused();
  });

  test("consumes the captured Undo once outside text editing", async ({ page }) => {
    let restores = 0;
    const deletions: string[] = [];
    await page.route("**/api/v1/models/batch/delete", (route) => {
      deletions.push(route.request().postData() ?? "");
      return route.fulfill({
        json: { succeeded_count: 1, failed_count: 0, succeeded_ids: [1], failed: [] },
      });
    });
    await page.route("**/api/v1/models/1/restore", async (route) => {
      restores += 1;
      const model = await page.request.get("/api/v1/models/1");
      await route.fulfill({ json: await model.json() });
    });
    await page.goto("/?c=maraio");
    await expect(page.getByRole("heading", { name: "maraio", exact: true })).toBeVisible();
    await page.keyboard.press("s");
    await page
      .getByRole("checkbox", { name: "Select skadis_kitchen-roll_screw", exact: true })
      .check();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeVisible();
    expect(deletions.map((body) => JSON.parse(body))).toEqual([{ model_ids: [1] }]);
    await page.keyboard.press("/");
    await page.keyboard.press("Control+z");
    expect(restores).toBe(0);
    await page.locator("header").getByRole("searchbox", { name: "Search library" }).blur();
    await page.keyboard.press("Control+z");
    await expect.poll(() => restores).toBe(1);
    await page.keyboard.press("Control+z");
    await expect(page.getByText("Models restored", { exact: true })).toBeVisible();
    expect(restores).toBe(1);
  });

  test("Escape preserves the safe detail return location", async ({ page }) => {
    await page.goto("/models/1?return=%2F%3Fc%3Dmaraio%26sort%3Dname-asc");
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect.poll(() => new URL(page.url()).pathname).toBe("/");
    expect(new URL(page.url()).searchParams.get("c")).toBe("maraio");
    expect(new URL(page.url()).searchParams.get("sort")).toBe("name-asc");
  });

  test("F2 opens the containing collection editor after returning", async ({ page }) => {
    await page.goto("/models/1");
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("F2");
    await expect(
      page
        .getByRole("dialog", { name: "Rename maraio", exact: true })
        .getByRole("textbox", { name: "Collection name" }),
    ).toBeFocused();
    expect(new URL(page.url()).searchParams.get("c")).toBe("maraio");
  });
});
