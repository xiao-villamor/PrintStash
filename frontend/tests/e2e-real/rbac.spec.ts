/**
 * What a non-admin user can actually see and do.
 *
 * Permissions are enforced in the backend and *reflected* in the UI, and the two can
 * disagree in both directions: a grid showing a collection the server would refuse, or an
 * edit button that does nothing. These tests drive the real UI against real grants, which
 * is the only place that disagreement shows up.
 */
import { test, expect, authBundleFor, authedContext } from "./helpers";
import {
  clickModelAction,
  createCollectionViaVault,
  modelActionItem,
  modelCard,
  uploadModel,
} from "./util";

const USER_PW = "userpass123";

// Grant `username` a role on collection `colName` through Settings.
async function grant(
  page: import("@playwright/test").Page,
  colName: string,
  username: string,
  roleLabel: "View" | "Edit",
) {
  await page.goto("/settings?section=access");
  const card = page.getByRole("group", { name: "Collection access" });
  await expect(card).toBeVisible();
  await card
    .getByRole("combobox")
    .filter({ has: page.getByRole("option", { name: "Select user" }) })
    .selectOption({ label: username });
  await card
    .getByRole("combobox")
    .filter({ has: page.getByRole("option", { name: "Select collection" }) })
    .selectOption({ label: colName });
  await card
    .getByRole("combobox")
    .filter({ has: page.getByRole("option", { name: "Admin", exact: true }) })
    .selectOption({ label: roleLabel });
  await Promise.all([
    page.waitForResponse(
      (r) => /\/collections\/\d+\/permissions\/\d+/.test(r.url()) && r.request().method() === "PUT",
    ),
    card.getByRole("button", { name: "Grant" }).click(),
  ]);
}

async function createUser(page: import("@playwright/test").Page, username: string) {
  await page.goto("/settings");
  await page.getByRole("button", { name: "Users & Access" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Initial password").fill(USER_PW);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: username })).toBeVisible();
}

// End-to-end RBAC: an admin creates two collections, a regular user, and grants
// that user access to ONLY one collection. The user must then see exactly that
// collection — never the other — proving backend collection scoping reaches the UI.
test.describe("role-based access", () => {
  test("a non-admin user sees only collections granted to them", async ({ page, browser }) => {
    const stamp = Date.now();
    const granted = `rbac-granted-${stamp}`;
    const hidden = `rbac-hidden-${stamp}`;
    const username = `rbac-user-${stamp}`;

    // ── Admin: create two collections ───────────────────────────────────────────
    for (const name of [granted, hidden]) {
      await createCollectionViaVault(page, name);
    }

    // ── Admin: create a regular user ────────────────────────────────────────────
    await page.goto("/settings");
    await page.getByRole("button", { name: "Users & Access" }).click();
    await page.getByLabel("Username").fill(username);
    await page.getByLabel("Initial password").fill(USER_PW);
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("paragraph").filter({ hasText: username })).toBeVisible();

    // ── Admin: grant the user "view" on the granted collection only ─────────────
    await grant(page, granted, username, "View");

    // ── User: open a separate browser and check visibility ──────────────────────
    const bundle = await authBundleFor(username, USER_PW);
    const { context, page: userPage } = await authedContext(browser, bundle);
    try {
      await userPage.goto("/");
      const sidebar = userPage.locator("aside");
      await expect(sidebar.getByRole("button", { name: granted, exact: true })).toBeVisible();
      await expect(sidebar.getByRole("button", { name: hidden, exact: true })).toHaveCount(0);
    } finally {
      await context.close();
    }
  });

  test("collection role gates whether a user can edit or delete a model", async ({
    page,
    browser,
  }) => {
    const stamp = Date.now();
    const col = `rbac-edit-${stamp}`;
    const model = `rbac-model-${stamp}`;
    const viewer = `rbac-viewer-${stamp}`;
    const editor = `rbac-editor-${stamp}`;

    // Admin: collection + a model inside it; capture the model URL.
    await createCollectionViaVault(page, col);
    await uploadModel(page, model, { collection: col });
    await modelCard(page, model).click();
    await expect(page.getByRole("heading", { name: model })).toBeVisible();
    const modelUrl = page.url();

    // Two users, two roles on the same collection.
    await createUser(page, viewer);
    await createUser(page, editor);
    await grant(page, col, viewer, "View");
    await grant(page, col, editor, "Edit");

    // Viewer: both Edit and Delete are present but disabled.
    const viewBundle = await authBundleFor(viewer, USER_PW);
    const v = await authedContext(browser, viewBundle);
    try {
      await v.page.goto(modelUrl);
      await expect(v.page.getByRole("heading", { name: model })).toBeVisible();
      await v.page.getByRole("button", { name: "Model actions" }).click();
      await expect(modelActionItem(v.page, "Edit details")).toBeDisabled();
      await expect(modelActionItem(v.page, "Delete model")).toBeDisabled();
    } finally {
      await v.context.close();
    }

    // Editor: can edit + save a rename, and can delete the model.
    const editBundle = await authBundleFor(editor, USER_PW);
    const e = await authedContext(browser, editBundle);
    try {
      await e.page.goto(modelUrl);
      await clickModelAction(e.page, "Edit details");
      const renamed = `${model}-edited`;
      await e.page.getByPlaceholder("Model name").fill(renamed);
      await e.page.getByRole("button", { name: /^Save$/ }).click();
      await expect(e.page.getByRole("heading", { name: renamed })).toBeVisible();

      // Delete is enabled for an edit-role user; deleting sends it to trash and
      // navigates away from the (now-gone) detail page.
      await clickModelAction(e.page, "Delete model");
      await e.page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
      await expect(e.page).not.toHaveURL(/\/models\//);
      await expect(e.page.getByRole("heading", { name: renamed })).toHaveCount(0);
    } finally {
      await e.context.close();
    }
  });
});

// A view-role user can open a model but not edit or delete it; an edit-role
// user can do both (Edit/Delete share the same canEditModel gate).
