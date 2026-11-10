/** Library inspection keeps native navigation, focused outcomes and Escape ownership observable. */
import { expect, test, type Page } from "@playwright/test";
import { apiPort, collectPageProblems, useMockApi } from "./_setup";

useMockApi();

async function inspect(page: Page) {
  await page.goto("/?c=maraio&type=all");
  const card = page
    .getByRole("main")
    .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
  await card.click();
  const drawer = page.locator("#model-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
  return { card, drawer };
}

async function records(page: Page) {
  const response = await page.request.get(`http://127.0.0.1:${apiPort}/api/v1/models/1/print-jobs`);
  expect(response.ok()).toBe(true);
  return response.json();
}

test.describe("Library inline inspector", () => {
  test("returns from the explicit full page to the same inspector", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const { drawer } = await inspect(page);
    const origin = page.url();
    expect(new URL(origin).pathname).toBe("/");
    await drawer.getByRole("link", { name: "Open page", exact: true }).click();
    await expect(page).toHaveURL(/\/models\/1(?:\?|$)/);
    await page.getByRole("link", { name: "Back", exact: true }).click();
    await expect(page).toHaveURL(origin);
    await expect(drawer).toBeVisible();
    await expect(
      drawer.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("persists one completed outcome while a numeric key is held", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const posts: unknown[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/models/1/print-jobs"
      )
        posts.push(request.postDataJSON());
    });
    const { drawer } = await inspect(page);
    await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
    const receipt = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/models/1/print-jobs",
    );
    await page.keyboard.down("1");
    expect((await receipt).ok()).toBe(true);
    await expect(drawer.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
    await page.keyboard.down("1");
    await page.keyboard.up("1");
    expect(posts).toEqual([
      expect.objectContaining({ file_id: 2, printer_id: 3, state: "completed", error: null }),
    ]);
    expect(await records(page)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 4,
          file_id: 2,
          printer_id: 3,
          state: "completed",
          source: "manual",
        }),
      ]),
    );
    expect(problems).toEqual([]);
  });

  test("persists the focused failure note through Mod Enter", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const { drawer } = await inspect(page);
    await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
    await page.keyboard.press("2");
    const note = drawer.getByRole("textbox", { name: /What went wrong/ });
    await expect(note).toBeFocused();
    await note.fill("Corner lifted at layer 112");
    const receipt = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/models/1/print-jobs",
    );
    await note.press("Control+Enter");
    expect((await receipt).ok()).toBe(true);
    await expect(note).toBeHidden();
    await drawer.getByRole("tab", { name: /History/ }).click();
    await expect(drawer.getByText("Corner lifted at layer 112", { exact: true })).toBeVisible();
    expect(await records(page)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 4,
          file_id: 2,
          printer_id: 3,
          state: "failed",
          error: "Corner lifted at layer 112",
        }),
      ]),
    );
    expect(problems).toEqual([]);
  });

  test("cancels failure before closing details before clearing selection", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.goto("/?c=maraio&type=all");
    const card = page
      .getByRole("main")
      .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
    await card.hover();
    const selection = page.getByRole("checkbox", {
      name: "Select skadis_kitchen-roll_screw",
      exact: true,
    });
    await selection.check();
    await page.keyboard.press("Control+Alt+b");
    const drawer = page.locator("#model-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
    await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
    await page.keyboard.press("2");
    const note = drawer.getByRole("textbox", { name: /What went wrong/ });
    await expect(note).toBeFocused();
    await note.fill("Unsaved note");
    await page.keyboard.press("Escape");
    await expect(note).toBeHidden();
    await expect(drawer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(selection).toBeChecked();
    await page.keyboard.press("Escape");
    await expect(selection).not.toBeChecked();
    expect(await records(page)).toHaveLength(3);
    expect(problems).toEqual([]);
  });
});
