/** Real account commands render authoritative state and preserve one-time credential intent. */
import { test, expect } from "./helpers";
import { collapseNavigationSidebar } from "./util";
import type { ApiKeyCreateResponse, ApiKeyRead, UserRead } from "../../src/types/auth";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("account administration", () => {
  test("administers an account through authoritative state", async ({ page }) => {
    const stamp = Date.now();
    const username = `e2e-account-${stamp}`;
    await page.goto("/settings?section=access");
    await collapseNavigationSidebar(page);
    await page.getByRole("button", { name: "Create user", exact: true }).click();
    await page.getByLabel("Username", { exact: true }).fill(username);
    const initialPassword = page.getByLabel("Initial password");
    const confirmation = page.getByLabel("Confirm password");
    await expect(page.getByLabel("Email (optional)")).toBeVisible();
    await initialPassword.fill("FakeInitialPassword123");
    await confirmation.fill("WrongPassword123");
    await expect(page.getByText("Passwords do not match.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Create", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Show passwords" }).click();
    await expect(initialPassword).toHaveAttribute("type", "text");
    await expect(confirmation).toHaveAttribute("type", "text");
    await page.getByRole("button", { name: "Hide passwords" }).click();
    await expect(initialPassword).toHaveAttribute("type", "password");
    await confirmation.fill("FakeInitialPassword123");
    const createdResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/admin/users" &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Create", exact: true }).click();
    const created = await createdResponse;
    expect(created.status()).toBe(201);
    const account: UserRead = await created.json();
    expect(account.email).toBeNull();
    const row = page.getByRole("group", { name: `User: ${username}`, exact: true });
    await expect(row).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Create user", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Create user", exact: true }).click();
    await expect(page.getByLabel("Username", { exact: true })).toHaveValue("");
    await page
      .getByRole("dialog", { name: "Create user", exact: true })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    const updatedResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/admin/users/${account.id}` &&
        response.request().method() === "PATCH",
    );
    const more = row.getByRole("button", { name: `More actions for ${username}` });
    await more.click();
    await page.getByRole("menuitem", { name: "Make admin" }).click();
    const updated = await updatedResponse;
    expect(updated.ok()).toBe(true);
    const promoted: UserRead = await updated.json();
    expect(promoted.is_superuser).toBe(true);
    await expect(row.getByText("Admin", { exact: true })).toBeVisible();
    await row.getByRole("button", { name: "Reset password", exact: true }).click();
    const reset = page.getByRole("dialog", { name: "Reset password", exact: true });
    await reset.getByLabel("New password").fill("FakeUpdatedPassword123");
    const resetResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/admin/users/${account.id}/password` &&
        response.request().method() === "POST",
    );
    await reset.getByRole("button", { name: "Reset password", exact: true }).click();
    expect((await resetResponse).ok()).toBe(true);
    await expect(reset).toHaveCount(0);
    const disabledResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/admin/users/${account.id}` &&
        response.request().method() === "DELETE",
    );
    await more.click();
    await page.getByRole("menuitem", { name: "Disable", exact: true }).click();
    expect((await disabledResponse).status()).toBe(204);
    await expect(row.getByText("Disabled", { exact: true })).toBeVisible();
    const users: UserRead[] = await (await page.request.get(`${API}/api/v1/admin/users`)).json();
    expect(users.find((user) => user.id === account.id)?.is_active).toBe(false);

    await more.click();
    await page.getByRole("menuitem", { name: "Delete user…" }).click();
    const deletion = page.getByRole("dialog", { name: "Delete user" });
    await deletion.getByRole("button", { name: "Cancel" }).click();
    await expect(row).toBeVisible();
    await more.click();
    await page.getByRole("menuitem", { name: "Delete user…" }).click();
    const removed = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === `/api/v1/admin/users/${account.id}` &&
        url.searchParams.get("remove") === "true" &&
        response.request().method() === "DELETE"
      );
    });
    await deletion.getByRole("button", { name: "Delete user" }).click();
    expect((await removed).status()).toBe(204);
    await expect(row).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("button", { name: "Create user", exact: true })).toBeEnabled();
    await expect(row).toHaveCount(0);

    const keyName = `e2e-account-key-${stamp}`;
    await page.getByLabel("Key name").fill(keyName);
    const keyField = await page.getByLabel("Key name").boundingBox();
    const generate = await page
      .getByRole("button", { name: "Generate", exact: true })
      .boundingBox();
    if (!keyField || !generate) throw new Error("API key controls must be visible");
    expect(
      Math.abs(keyField.y + keyField.height - generate.y - generate.height),
    ).toBeLessThanOrEqual(1);
    const keyResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/auth/api-keys" &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Generate", exact: true }).click();
    const minted: ApiKeyCreateResponse = await (await keyResponse).json();
    await expect(page.getByTitle("Copy API key", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate", exact: true })).toBeDisabled();
    const keyRow = page.getByRole("group", { name: `API key: ${keyName}`, exact: true });
    await expect(keyRow).toBeVisible();
    await page.getByRole("button", { name: "Dismiss this secret", exact: true }).click();
    await expect(page.getByTitle("Copy API key", { exact: true })).toHaveCount(0);
    await expect(keyRow).toBeVisible();
    const revokeResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/auth/api-keys/${minted.id}` &&
        response.request().method() === "DELETE",
    );
    await keyRow.getByRole("button", { name: "Revoke API key", exact: true }).click();
    expect((await revokeResponse).status()).toBe(204);
    await expect(keyRow).toHaveCount(0);
    const keys: ApiKeyRead[] = await (await page.request.get(`${API}/api/v1/auth/api-keys`)).json();
    expect(keys.some((key) => key.id === minted.id)).toBe(false);
  });
});
