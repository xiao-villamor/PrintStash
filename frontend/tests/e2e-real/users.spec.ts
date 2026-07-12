import { test, expect, authBundleFor } from "./helpers";

test("admin can promote, disable, and reset a user", async ({ page }) => {
  const username = `e2e-mgmt-${Date.now()}`;
  await page.goto("/settings");
  await page.getByRole("button", { name: "Users & Access" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Initial password").fill("initpass123");
  await page.getByRole("button", { name: "Create" }).click();

  // The user's row: scoped by its username + its own password-reset field.
  const row = () =>
    page
      .locator("div")
      .filter({ has: page.getByRole("paragraph").filter({ hasText: username }) })
      .filter({ has: page.getByPlaceholder("New password") })
      .last();
  await expect(row()).toBeVisible();

  // Promote to admin and back.
  await row().getByRole("button", { name: "Make admin" }).click();
  await expect(row().getByText("Admin", { exact: true })).toBeVisible();
  await row().getByRole("button", { name: "Remove admin" }).click();
  await expect(row().getByText("Admin", { exact: true })).toHaveCount(0);

  // Disable and re-enable.
  await row().getByRole("button", { name: "Disable" }).click();
  await expect(row().getByText("Disabled", { exact: true })).toBeVisible();
  await row().getByRole("button", { name: "Enable" }).click();
  await expect(row().getByText("Disabled", { exact: true })).toHaveCount(0);

  // Reset the password; the new one then authenticates against the real backend.
  await row().getByPlaceholder("New password").fill("newpass4567");
  await row().getByRole("button", { name: "Reset password" }).click();
  await expect
    .poll(async () => {
      try {
        await authBundleFor(username, "newpass4567");
        return true;
      } catch {
        return false;
      }
    })
    .toBe(true);
});
