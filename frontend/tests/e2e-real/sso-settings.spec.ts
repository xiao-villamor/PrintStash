/**
 * Configuring OIDC, and the secret that must never come back out.
 *
 * The client secret is written once and never returned by the API, so the settings form
 * has to handle "unchanged" without ever displaying it — a form that round-trips the
 * secret shows a credential to anyone who can open the page. The login button appearing
 * is the proof the configuration actually took effect.
 */
import { test, expect } from "./helpers";

// SSO / OIDC (0.11.0): a superuser can configure OIDC without a real IdP —
// settings persist, and the client secret is never echoed back to the client
// (mirrors the backend contract in test_oidc.py's
// test_superuser_can_configure_oidc_without_secret_disclosure). Once enabled,
// the login page grows an SSO button labeled with the configured display name.

test.describe("SSO settings", () => {
  test("configure OIDC in Settings, secret never round-trips, login page shows the SSO button", async ({
    page,
    browser,
  }) => {
    const displayName = `e2e-sso-${Date.now()}`;

    await page.goto("/settings?section=sso");
    await expect(page.getByRole("heading", { name: "OpenID Connect" })).toBeVisible();

    await page.getByLabel("Issuer URL").fill("https://auth.example.test/application/o/printstash");
    await page.getByLabel("Client ID").fill("printstash-e2e");
    await page.getByLabel("Client secret", { exact: true }).fill("super-secret-value");
    await page.getByLabel("Login button label").fill(displayName);
    await page.getByRole("checkbox", { name: "Enable SSO login" }).click();

    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/config") && r.request().method() === "PUT",
      ),
      page.getByRole("button", { name: "Save SSO settings" }).click(),
    ]);

    // Reload: settings persisted, and the secret field never comes back prefilled
    // with the real value — only a "configured" placeholder.
    await page.reload();
    await expect(page.getByLabel("Issuer URL")).toHaveValue(
      "https://auth.example.test/application/o/printstash",
    );
    await expect(page.getByLabel("Client ID")).toHaveValue("printstash-e2e");
    await expect(page.getByLabel("Client secret", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("Client secret", { exact: true })).toHaveAttribute(
      "placeholder",
      "Configured — enter to replace",
    );
    const html = await page.content();
    expect(html).not.toContain("super-secret-value");

    // The login page now offers the configured SSO button. Check this from a
    // brand-new, unauthenticated context rather than logging the shared `page`
    // out: a real logout bumps the backend's `auth_version` for this user,
    // which invalidates every previously-issued token for them — including the
    // one `helpers.ts` caches and reuses across every other spec in this
    // serial suite. That's not a hypothetical: it broke tags.spec.ts (the next
    // file alphabetically) with "Session expired" until this was isolated.
    const loggedOutContext = await browser.newContext();
    try {
      const loginPage = await loggedOutContext.newPage();
      await loginPage.goto("/");
      await expect(loginPage).toHaveURL(/\/login/);
      await expect(loginPage.getByRole("button", { name: new RegExp(displayName) })).toBeVisible();
    } finally {
      await loggedOutContext.close();
    }

    // Cleanup: disable SSO so the shared DB doesn't drift for later runs and the
    // login page for other specs stays local-only.
    await page.goto("/settings?section=sso");
    await page.getByRole("checkbox", { name: "Enable SSO login" }).click();
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/config") && r.request().method() === "PUT",
      ),
      page.getByRole("button", { name: "Save SSO settings" }).click(),
    ]);
  });
});
