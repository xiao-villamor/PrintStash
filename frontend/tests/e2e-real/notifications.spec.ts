/** Notification drafts and switch intents use conditional writes against the real API. */
import { test, expect } from "./helpers";
import type { NotificationChannel, NotificationsSettings } from "../../src/types/notifications";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("Notification editing", () => {
  test("resolves competing notification editors", async ({ page, context }) => {
    const name = `notification-${Date.now()}`;
    const created = await page.request.post(`${API}/api/v1/notifications/channels`, {
      data: {
        name,
        target: "webhook",
        config: { url: "https://example.com/unused-notification-hook" },
        events: ["print_completed"],
      },
    });
    expect(created.status()).toBe(201);
    const channel: NotificationChannel = await created.json();
    const second = await context.newPage();
    const release = Promise.withResolvers<void>();
    try {
      await page.goto("/settings?section=notifications");
      await second.goto("/settings?section=notifications");
      await page.getByTitle("Edit channel").click();
      await second.getByTitle("Edit channel").click();
      await page.getByPlaceholder("Living-room printer alerts").fill(`${name}-winner`);
      await page.getByRole("checkbox", { name: "Printer offline", exact: true }).check();
      await second.getByPlaceholder("Living-room printer alerts").fill(`${name}-revised`);

      const path = `/api/v1/notifications/channels/${channel.id}`;
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
      await expect(second.getByPlaceholder("Living-room printer alerts")).toHaveValue(
        `${name}-revised`,
      );
      await expect(
        second.getByRole("button", { name: "Save changes", exact: true }),
      ).toBeDisabled();

      await second.getByRole("button", { name: "Review current values" }).click();
      await expect(second.getByText(`Current channel: ${name}-winner`)).toBeVisible();
      const revised = second.waitForResponse(
        (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
      );
      await second.getByRole("button", { name: "Save revised changes" }).click();
      expect((await revised).status()).toBe(200);
      await expect(second.getByText(`${name}-revised`, { exact: true })).toBeVisible();
      const persisted: NotificationsSettings = await (
        await page.request.get(`${API}/api/v1/notifications`)
      ).json();
      expect(persisted.channels.find((row) => row.id === channel.id)).toMatchObject({
        name: `${name}-revised`,
        events: ["print_completed", "printer_offline"],
      });

      // Hold a real request after its gesture captured the old switch base.
      // The competing browser commits before that request reaches FastAPI.
      const held = Promise.withResolvers<void>();
      await second.route("**/api/v1/notifications", async (route) => {
        if (route.request().method() === "PUT") {
          held.resolve();
          await release.promise;
        }
        await route.continue();
      });
      const switchConflict = second.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/notifications") && response.request().method() === "PUT",
      );
      await second.getByRole("checkbox").click();
      await held.promise;
      const switchAccepted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/notifications") && response.request().method() === "PUT",
      );
      await page.getByRole("checkbox").click();
      expect((await switchAccepted).status()).toBe(200);
      release.resolve();
      expect((await switchConflict).status()).toBe(412);
      await second.getByRole("button", { name: "Review current values" }).click();
      const switchRevised = second.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/notifications") && response.request().method() === "PUT",
      );
      await second.getByRole("button", { name: "Save revised changes" }).click();
      expect((await switchRevised).status()).toBe(200);
      await expect(second.getByRole("checkbox")).toBeChecked();
    } finally {
      release.resolve();
      await second.close();
      await page.request.delete(`${API}/api/v1/notifications/channels/${channel.id}`);
      await page.request.put(`${API}/api/v1/notifications`, { data: { enabled: false } });
    }
  });
});
