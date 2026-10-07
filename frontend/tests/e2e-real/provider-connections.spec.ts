/** Pairing and competing browser-name edits use the real account-scoped API. */
import { test, expect } from "./helpers";
import type {
  BrowserDeviceRead,
  BrowserPairingCreateRead,
} from "../../src/types/provider-connections";
const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("Paired browser settings", () => {
  test("detects competing edits in real browsers", async ({ page, context, browser }) => {
    const name = `paired-${Date.now()}`;
    const second = await context.newPage();
    const extension = await browser.newContext();
    let device: BrowserDeviceRead | null = null;
    try {
      // The UI issues a temporary code; a browser without an account session claims it.
      await page.goto("/settings?section=imports");
      const issued = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/browser-pairings") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Create pairing code" }).click();
      const pairing: BrowserPairingCreateRead = await (await issued).json();
      await expect(page.getByText(pairing.code, { exact: true })).toBeVisible();
      const claimed = await extension.request.post(`${API}/api/v1/browser-pairings/claim`, {
        data: { code: pairing.code, name },
      });
      expect(claimed.ok()).toBe(true);
      const claim: { credential: string; device: BrowserDeviceRead } = await claimed.json();
      device = claim.device;
      expect(claim.credential.length).toBeGreaterThan(0);
      const listing = await page.request.get(`${API}/api/v1/browser-pairings`);
      expect(await listing.text()).not.toContain(claim.credential);

      // Both editors type from one version before the first confirmation.
      await page.reload();
      await second.goto("/settings?section=imports");
      const firstInput = page.getByRole("textbox", { name: `Browser name for ${name}` });
      const secondInput = second.getByRole("textbox", { name: `Browser name for ${name}` });
      await firstInput.fill(`${name}-winner`);
      await secondInput.fill(`${name}-revised`);
      const path = `/api/v1/browser-pairings/${device.id}`;
      const accepted = page.waitForResponse(
        (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "Save browser name", exact: true }).click();
      expect((await accepted).status()).toBe(200);
      const conflict = second.waitForResponse(
        (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
      );
      await second.getByRole("button", { name: "Save browser name", exact: true }).click();
      expect((await conflict).status()).toBe(412);
      await expect(secondInput).toHaveValue(`${name}-revised`);
      await expect(
        second.getByRole("button", { name: "Save browser name", exact: true }),
      ).toBeDisabled();
      await second.getByRole("button", { name: "Review current values" }).click();
      await expect(second.getByText(`Current browser name: ${name}-winner`)).toBeVisible();
      const revised = second.waitForResponse(
        (response) => response.url().endsWith(path) && response.request().method() === "PATCH",
      );
      await second.getByRole("button", { name: "Save revised changes" }).click();
      expect((await revised).status()).toBe(200);
      await expect(
        second.getByRole("textbox", { name: `Browser name for ${name}-revised` }),
      ).toHaveValue(`${name}-revised`);
      const persisted: BrowserDeviceRead[] = await (
        await page.request.get(`${API}/api/v1/browser-pairings`)
      ).json();
      expect(persisted.find((row) => row.id === device?.id)?.name).toBe(`${name}-revised`);

      // Revocation still requires confirmation and reconciles the server record.
      await second.getByRole("button", { name: `Revoke ${name}-revised`, exact: true }).click();
      await second
        .getByRole("dialog")
        .getByRole("button", { name: "Revoke browser", exact: true })
        .click();
      await expect(
        second.getByRole("textbox", { name: `Browser name for ${name}-revised` }),
      ).toBeDisabled();
    } finally {
      if (device) await page.request.delete(`${API}/api/v1/browser-pairings/${device.id}`);
      await second.close();
      await extension.close();
    }
  });
});
