import { collectPageProblems, expect, installMockApiHooks, test } from "./helpers";

installMockApiHooks();

test("desktop navigation reaches Pending Imports and marks nested routes active", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.getByRole("button", { name: "tester" }).click();

  const pending = page.getByRole("menuitem", { name: "Pending" });
  await expect(pending).toHaveAttribute("href", "/inbox");
  await pending.click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "Pending Imports" })).toBeVisible();

  await page.getByRole("button", { name: "tester" }).click();
  await expect(page.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  await page.goto("/inbox/41");
  await expect(page).toHaveURL(/\/inbox\/41$/);
  await page.getByRole("button", { name: "tester" }).click();
  await expect(page.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("mobile navigation reaches Pending Imports and stays active on detail routes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const pending = page.getByRole("link", { name: "Pending" });
  await expect(pending).toHaveAttribute("href", "/inbox");
  await pending.click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "Pending Imports" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute("aria-current", "page");

  await page.goto("/inbox/41");
  await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute("aria-current", "page");
});

test("pending imports render as a responsive review queue", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "From URL" }).click();
  await page
    .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
    .fill("https://www.printables.com/model/41-capture-bracket");
  await page.getByRole("button", { name: "Review URL" }).click();
  await page.goto("/inbox");
  const problems = await collectPageProblems(page);

  const queue = page.getByRole("list", { name: "Import queue" });
  await expect(queue.getByRole("heading", { name: "Capture bracket" })).toBeVisible();
  await expect(queue.getByText("Printables")).toBeVisible();
  await expect(queue.getByText("Files: 2")).toBeVisible();
  await expect(queue.getByRole("link", { name: "Review" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  expect(problems).toEqual([]);
});

test("pending imports can be deleted and completed jobs can be cleared", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "From URL" }).click();
  await page
    .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
    .fill("https://www.printables.com/model/41-capture-bracket");
  await page.getByRole("button", { name: "Review URL" }).click();
  await page.goto("/inbox");

  await page.getByRole("button", { name: "Delete import" }).click();
  await page
    .getByRole("dialog", { name: "Delete pending import?" })
    .getByRole("button", { name: "Delete import" })
    .click();
  await expect(page.getByText("No imports in the queue")).toBeVisible();

  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "From URL" }).click();
  await page
    .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
    .fill("https://www.printables.com/model/41-capture-bracket");
  await page.getByRole("button", { name: "Review URL" }).click();
  await page.getByRole("button", { name: "Import selected" }).click();
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

test("URL capture is reviewable, reports a partial result, and restores a source override", async ({
  page,
}) => {
  const problems = await collectPageProblems(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "From URL" }).click();
  await page
    .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
    .fill("https://www.printables.com/model/41-capture-bracket");
  await page.getByRole("button", { name: "Review URL" }).click();

  await expect(page).toHaveURL(/\/inbox\/41$/);
  await expect(page.getByRole("heading", { name: "Capture bracket" })).toBeVisible();
  const files = page.getByRole("group", { name: "Files to import" });
  await expect(files.getByRole("checkbox", { name: "Select capture-bracket.stl" })).toBeChecked();
  await files.getByRole("checkbox", { name: "Select capture-bracket.3mf" }).press("Space");
  await expect(page.getByRole("button", { name: "Import selected" })).toBeEnabled();
  await page.getByRole("button", { name: "Import selected" }).click();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await expect(page.getByText("capture-bracket.3mf")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry failed files" })).toBeVisible();
  expect(problems, "before opening the imported model").toEqual([]);

  await page.getByRole("link", { name: "Open model" }).click();
  expect(problems, "before opening the Source tab").toEqual([]);
  await page.getByRole("tab", { name: "Source" }).click();
  await expect(page.getByText("Fixture maker")).toBeVisible();
  await expect(page.getByText("CC BY 4.0")).toBeVisible();
  await expect(page.getByText("Print with supports.")).toBeVisible();
  expect(problems, "after opening the Source tab").toEqual([]);
  const creatorField = page
    .getByRole("heading", { name: "Creator", exact: true, level: 3 })
    .locator("../..");
  await creatorField.getByRole("button", { name: "Edit" }).click();
  await creatorField.getByLabel("Creator override").fill("Corrected maker");
  await creatorField.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Corrected maker")).toBeVisible();
  await expect(page.getByText("Edited")).toBeVisible();
  expect(problems, "after saving the override").toEqual([]);
  await creatorField.getByRole("button", { name: "Edit" }).click();
  await creatorField.getByRole("button", { name: "Restore captured value" }).click();
  const dialog = page.getByRole("dialog", { name: "Restore captured value?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByText("Fixture maker")).toBeVisible();
  await expect(page.getByText("Source").last()).toBeVisible();

  expect(problems).toEqual([]);
});

test("pending import defaults its collection to the captured title", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "From URL" }).click();
  await page
    .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
    .fill("https://www.printables.com/model/41-capture-bracket");
  await page.getByRole("button", { name: "Review URL" }).click();

  await expect(page.getByRole("combobox", { name: "Destination" })).toHaveValue("new");
  await expect(page.getByRole("textbox", { name: "Collection name" })).toHaveValue(
    "Capture bracket",
  );
  await page.getByRole("button", { name: "Import selected" }).click();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  expect(problems).toEqual([]);
});

test("pending import can be deleted with its staged capture", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "From URL" }).click();
  await page
    .getByPlaceholder("Model page, collection, or direct .stl/.zip link")
    .fill("https://www.printables.com/model/41-capture-bracket");
  await page.getByRole("button", { name: "Review URL" }).click();
  await expect(page).toHaveURL(/\/inbox\/41$/);

  await page.getByRole("button", { name: "Delete import" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete pending import?" });
  await expect(dialog).toContainText("deletes its staged files");
  await dialog.getByRole("button", { name: "Delete import" }).click();

  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByText("No imports in the queue", { exact: true })).toBeVisible();
});
