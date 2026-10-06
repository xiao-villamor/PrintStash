/** Real account commands render authoritative state and preserve one-time credential intent. */
import { test, expect } from "./helpers";
import type { ApiKeyCreateResponse, ApiKeyRead, UserRead } from "../../src/types/auth";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("account administration", () => {
  test("administers an account through authoritative state", async ({ page }) => {
    const stamp = Date.now();
    const username = `e2e-account-${stamp}`;
    await page.goto("/settings?section=access");
    await page.getByLabel("Username", { exact: true }).fill(username);
    await page.getByLabel("Initial password").fill("FakeInitialPassword123");
    const createdResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/admin/users" &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Create", exact: true }).click();
    const created = await createdResponse;
    expect(created.status()).toBe(201);
    const account: UserRead = await created.json();
    const row = page.getByRole("group", { name: `User: ${username}`, exact: true });
    await expect(row).toBeVisible();
    await expect(page.getByLabel("Username", { exact: true })).toHaveValue("");
    const updatedResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/admin/users/${account.id}` &&
        response.request().method() === "PATCH",
    );
    await row.getByRole("button", { name: "Make admin" }).click();
    const updated = await updatedResponse;
    expect(updated.ok()).toBe(true);
    const promoted: UserRead = await updated.json();
    expect(promoted.is_superuser).toBe(true);
    await expect(row.getByText("Admin", { exact: true })).toBeVisible();
    await row.getByPlaceholder("New password").fill("FakeUpdatedPassword123");
    const resetResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/admin/users/${account.id}/password` &&
        response.request().method() === "POST",
    );
    await row.getByRole("button", { name: "Reset password" }).click();
    expect((await resetResponse).ok()).toBe(true);
    await expect(row.getByPlaceholder("New password")).toHaveValue("");
    const disabledResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/admin/users/${account.id}` &&
        response.request().method() === "DELETE",
    );
    await row.getByRole("button", { name: "Disable", exact: true }).click();
    expect((await disabledResponse).status()).toBe(204);
    await expect(row.getByText("Disabled", { exact: true })).toBeVisible();
    const users: UserRead[] = await (await page.request.get(`${API}/api/v1/admin/users`)).json();
    expect(users.find((user) => user.id === account.id)?.is_active).toBe(false);

    const keyName = `e2e-account-key-${stamp}`;
    await page.getByLabel("Key name").fill(keyName);
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
    await keyRow.getByTitle("Revoke API key", { exact: true }).click();
    expect((await revokeResponse).status()).toBe(204);
    await expect(keyRow).toHaveCount(0);
    const keys: ApiKeyRead[] = await (await page.request.get(`${API}/api/v1/auth/api-keys`)).json();
    expect(keys.some((key) => key.id === minted.id)).toBe(false);
  });
});
