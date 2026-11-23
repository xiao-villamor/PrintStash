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
    await row.getByRole("button", { name: "Make admin" }).click();
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
    await row.getByRole("button", { name: "Disable", exact: true }).click();
    expect((await disabledResponse).status()).toBe(204);
    await expect(row.getByText("Disabled", { exact: true })).toBeVisible();
    const users: UserRead[] = await (await page.request.get(`${API}/api/v1/admin/users`)).json();
    expect(users.find((user) => user.id === account.id)?.is_active).toBe(false);

    const keyName = `e2e-account-key-${stamp}`;
    const keyField = page.getByLabel("Key name");
    const generate = page.getByRole("button", { name: "Generate", exact: true });
    await keyField.fill(keyName);
    const keyFieldBox = await keyField.boundingBox();
    const generateBox = await generate.boundingBox();
    if (keyFieldBox === null || generateBox === null) throw new Error("Key form is hidden");
    expect(
      Math.abs(keyFieldBox.y + keyFieldBox.height - generateBox.y - generateBox.height),
    ).toBeLessThanOrEqual(1);
    const keyResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/auth/api-keys" &&
        response.request().method() === "POST",
    );
    await generate.click();
    const minted: ApiKeyCreateResponse = await (await keyResponse).json();
    const copy = page.getByRole("button", { name: "Copy API key", exact: true });
    await expect(copy).toBeVisible();
    await expect(generate).toBeDisabled();
    const dismiss = page.getByRole("button", { name: "Dismiss this secret", exact: true });
    const notice = page.getByText("Copy this key now. It will only be shown once.", {
      exact: true,
    });
    const dismissBox = await dismiss.boundingBox();
    const noticeBox = await notice.boundingBox();
    if (dismissBox === null || noticeBox === null) throw new Error("Key receipt is hidden");
    expect(
      Math.abs(dismissBox.y + dismissBox.height / 2 - noticeBox.y - noticeBox.height / 2),
    ).toBeLessThanOrEqual(1);
    const keyRow = page.getByRole("group", { name: `API key: ${keyName}`, exact: true });
    await expect(keyRow).toBeVisible();
    await dismiss.click();
    await expect(copy).toHaveCount(0);
    await expect(generate).toBeEnabled();
    await page.reload();
    await expect(keyRow).toBeVisible();
    await expect(copy).toHaveCount(0);
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
