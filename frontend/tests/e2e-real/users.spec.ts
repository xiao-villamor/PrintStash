/**
 * Administering the people who share a deployment.
 *
 * Promote, disable, reset — the three acts that change what somebody else can do. They
 * run against the real backend because each is enforced server-side and the UI only
 * reflects it; a disabled user who can still sign in is the failure mode, and it is
 * invisible from the admin screen.
 */
import { test, expect, authBundleFor } from "./helpers";

test.describe("users and access", () => {
  test("admin can promote, disable, and reset a user", async ({ page }) => {
    const username = `e2e-mgmt-${Date.now()}`;
    await page.goto("/settings");
    await page.getByRole("button", { name: "Users & Access" }).click();
    await page.getByRole("button", { name: "Create user", exact: true }).click();
    await page.getByLabel("Username").fill(username);
    await page.getByLabel("Initial password").fill("initpass123");
    await page.getByRole("button", { name: "Create", exact: true }).click();

    const row = () => page.getByRole("group", { name: `User: ${username}`, exact: true });
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
    await row().getByRole("button", { name: "Reset password", exact: true }).click();
    const reset = page.getByRole("dialog", { name: "Reset password", exact: true });
    await reset.getByLabel("New password").fill("newpass4567");
    await reset.getByRole("button", { name: "Reset password", exact: true }).click();
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
});
