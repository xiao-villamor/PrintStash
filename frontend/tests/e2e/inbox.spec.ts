/*
 * The pending-imports queue as a page, including the two destructive actions.
 *
 * The queue is responsive because it is a work list somebody triages on a phone
 * in a workshop, so the layout is part of the contract rather than polish.
 *
 * The rest is about what deletion touches. Deleting a pending import must take
 * its staged capture with it — bytes staged with no row that owns them are a leak
 * nothing will ever collect. Clearing *completed* jobs must not touch the models
 * they produced, which is the difference between tidying a queue and deleting a
 * library. And a new import defaults its collection to the captured title, which
 * is the decision that makes a browser capture one click rather than three.
 */
import { expect, test, type Page } from "@playwright/test";

import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

async function seedCapture(page: Page) {
  const response = await page.request.post("/api/v1/inbox", {
    data: { url: "https://www.printables.com/model/41-capture-bracket" },
  });
  expect(response.status()).toBe(201);
}

test.describe("pending imports", () => {
  test("pending imports render as a responsive review queue", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await seedCapture(page);
    await page.goto("/inbox");

    const queue = page.getByRole("list", { name: "Captures" });
    await expect(queue.getByRole("heading", { name: "Capture bracket" })).toBeVisible();
    await expect(queue.getByText("Printables")).toBeVisible();
    await expect(queue.getByText("Files: 2")).toBeVisible();
    await expect(queue.getByRole("link", { name: "Review" })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    expect(problems).toEqual([]);
  });

  test("pending imports can be deleted and completed jobs can be cleared", async ({ page }) => {
    await seedCapture(page);
    await page.goto("/inbox");

    await page.getByRole("button", { name: "Delete import" }).click();
    await page
      .getByRole("dialog", { name: "Delete pending import?" })
      .getByRole("button", { name: "Delete import" })
      .click();
    await expect(page.getByText("No imports in the queue")).toBeVisible();

    await seedCapture(page);
    await page.goto("/inbox/41");
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
    await page.goto("/inbox");

    await page.getByRole("tab", { name: /Completed/ }).click();
    await page.getByRole("button", { name: "Clear completed" }).click();
    await page
      .getByRole("dialog", { name: "Clear completed imports?" })
      .getByRole("button", { name: "Clear completed" })
      .click();
    await expect(page.getByText("No completed imports")).toBeVisible();
  });

  test("imports a selection from the queue using its saved choices", async ({ page }) => {
    await seedCapture(page);
    await page.goto("/inbox");
    const queue = page.getByRole("list", { name: "Captures" });
    await queue.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Import selected" }).click();
    await expect(page.getByText("No imports in the queue")).toBeVisible();
    await page.getByRole("tab", { name: /Completed/ }).click();
    await expect(page.getByRole("heading", { name: "Capture bracket" })).toBeVisible();
    await expect(page.getByRole("link", { name: "View", exact: true })).toHaveAttribute(
      "href",
      "/inbox/41",
    );
  });

  test("pending import defaults its collection to the captured title", async ({ page }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await page.getByRole("tab", { name: "From URL" }).click();
    await page
      .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
      .fill("https://www.printables.com/model/41-capture-bracket");
    await page.getByRole("button", { name: "Capture for review" }).click();
    await expect(page).toHaveURL(/\/inbox\/\d+$/);
    // Move off the notification so its hover pause cannot cover the next action.
    await page.mouse.move(0, 0);
    await expect(
      page.getByText("Captured URL. Review it before importing.", { exact: true }),
    ).toBeHidden();

    await page.getByRole("button", { name: "Edit destination" }).click();
    await expect(page.getByRole("combobox", { name: "Collection", exact: true })).toHaveValue(
      "new",
    );
    await expect(page.getByRole("textbox", { name: "Collection name" })).toHaveValue(
      "Capture bracket",
    );
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("pending import can be deleted with its staged capture", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await page.getByRole("tab", { name: "From URL" }).click();
    await page
      .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
      .fill("https://www.printables.com/model/41-capture-bracket");
    await page.getByRole("button", { name: "Capture for review" }).click();
    await expect(page).toHaveURL(/\/inbox\/\d+$/);
    // Move off the notification so its hover pause cannot cover the next action.
    await page.mouse.move(0, 0);
    await expect(
      page.getByText("Captured URL. Review it before importing.", { exact: true }),
    ).toBeHidden();
    await expect(page).toHaveURL(/\/inbox\/41$/);

    await page.getByRole("button", { name: "Delete import" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete pending import?" });
    await expect(dialog).toContainText("deletes its staged files");
    await dialog.getByRole("button", { name: "Delete import" }).click();

    await expect(page).toHaveURL(/\/inbox$/);
    // The queue tab's own empty state, not the page-level one: the Inbox is
    // tabbed, so "there are no pending imports" is a statement about the queue.
    await expect(page.getByText("No imports in the queue", { exact: true })).toBeVisible();
  });
});
