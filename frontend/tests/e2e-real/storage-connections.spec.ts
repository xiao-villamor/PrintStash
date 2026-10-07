/** Reusable storage edits preserve deliberate intent across competing administrators. */
import { test, expect } from "./helpers";
import type { StorageConnection } from "../../src/types";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test("resolves competing connection editors", async ({ page, context }) => {
  const name = `connection-${Date.now()}`;
  const created = await page.request.post(`${API}/api/v1/storage-connections`, {
    data: {
      name,
      kind: "s3",
      purpose: "both",
      configuration: { bucket: "unused-e2e-bucket", root: "PrintStash" },
      secrets: { access_key: "FakeBrowserAccessKey", secret_key: "FakeBrowserSecretKey" },
    },
  });
  expect(created.status()).toBe(201);
  const connection: StorageConnection = await created.json();
  const second = await context.newPage();
  try {
    await page.goto("/settings?section=remote-storage");
    await second.goto("/settings?section=remote-storage");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await second.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByLabel("Connection name", { exact: true }).fill(`${name}-winner`);
    await page.getByRole("button", { name: "Backup replicas", exact: true }).click();
    await second.getByLabel("Connection name", { exact: true }).fill(`${name}-revised`);
    const path = `/api/v1/storage-connections/${connection.id}`;
    const accepted = page.waitForResponse(
      (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    expect((await accepted).status()).toBe(200);
    const conflict = second.waitForResponse(
      (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
    );
    await second.getByRole("button", { name: "Save changes", exact: true }).click();
    expect((await conflict).status()).toBe(412);
    await expect(second.getByLabel("Connection name", { exact: true })).toHaveValue(
      `${name}-revised`,
    );
    await expect(second.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
    await second.getByRole("button", { name: "Review current values" }).click();
    await expect(
      second.getByRole("region", { name: "Latest saved version" }).getByText(`${name}-winner`),
    ).toBeVisible();
    const revised = second.waitForResponse(
      (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
    );
    await second.getByRole("button", { name: "Save revised changes" }).click();
    expect((await revised).status()).toBe(200);
    await expect(second.getByRole("combobox", { name: `Use ${name}-revised for` })).toHaveValue(
      "backup",
    );
    const rows: StorageConnection[] = await (
      await page.request.get(`${API}/api/v1/storage-connections`)
    ).json();
    expect(rows.find((row) => row.id === connection.id)).toMatchObject({
      name: `${name}-revised`,
      purpose: "backup",
      secret_fields_set: ["access_key", "secret_key"],
    });
  } finally {
    await second.close();
    await page.request.delete(`${API}/api/v1/storage-connections/${connection.id}`);
  }
});
