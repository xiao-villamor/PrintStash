/*
 * Handing authentication to an identity provider.
 *
 * Turning this on moves the front door: after it, the login page offers a
 * provider button and group membership decides who is an admin. So the two
 * fields the exchange cannot happen without — the issuer URL and the client ID —
 * are checked before the switch is allowed to mean anything. Saving "enabled"
 * with neither produces a login page whose SSO button leads nowhere, and the
 * only way back is the local form beside it.
 *
 * The client secret follows the same rule as every other stored credential
 * here: never returned, never sent back unless it was retyped. Clearing it is a
 * separate, explicit action, because "leave blank to keep" and "blank means
 * remove" cannot both be true of one empty field.
 *
 * `allow_insecure_http` exists for a provider on a LAN with no certificate. It
 * is off by default and stays that way unless asked for — an issuer reached
 * over plain HTTP is an authentication flow anybody on the network can read.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OidcSettingsCard } from "@/components/oidc-settings-card";
import { json, renderApp } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";
import { aVaultConfig } from "@/test-support/factories";
import type { VaultConfigRead, VaultConfigUpdate } from "@/types";

type OidcConfig = Pick<
  VaultConfigRead,
  | "oidc_enabled"
  | "oidc_issuer_url"
  | "oidc_client_id"
  | "has_oidc_client_secret"
  | "oidc_scopes"
  | "oidc_username_claim"
  | "oidc_groups_claim"
  | "oidc_admin_groups"
  | "oidc_display_name"
  | "oidc_redirect_uri"
  | "oidc_allow_insecure_http"
>;

function aConfig(over: Partial<OidcConfig> = {}): VaultConfigRead {
  return aVaultConfig(over);
}
function renderCard(over: Partial<OidcConfig> = {}) {
  const config = aConfig(over);
  const saveConfig = vi
    .fn<(payload: VaultConfigUpdate) => Promise<VaultConfigRead>>()
    .mockResolvedValue(config);
  const result = renderApp(<OidcSettingsCard />, {
    routes: {
      "GET /api/v1/config": json(config),
      "PUT /api/v1/config": async (_url, init) =>
        json(await saveConfig(JSON.parse(String(init?.body)))),
    },
  });
  return { ...result, saveConfig };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OidcSettingsCard", () => {
  describe("what it shows", () => {
    it("fills the form from the saved configuration", async () => {
      renderCard({ oidc_issuer_url: "https://auth.test/o/printstash" });

      expect(await screen.findByDisplayValue("https://auth.test/o/printstash")).toBeInTheDocument();
    });

    it("reads as off when SSO is not configured", async () => {
      renderCard();

      expect(await screen.findByRole("checkbox", { name: "Enable SSO login" })).toHaveAttribute(
        "aria-checked",
        "false",
      );
    });

    it("keeps the insecure-issuer escape hatch off by default", async () => {
      // An issuer reached over plain HTTP is an authentication flow anybody on
      // the network can read.
      renderCard();

      expect(
        await screen.findByRole("checkbox", { name: "Allow insecure HTTP issuer" }),
      ).toHaveAttribute("aria-checked", "false");
    });
  });

  describe("enabling it", () => {
    it("refuses without an issuer URL", async () => {
      // Saving "enabled" with nothing behind it produces a login page whose SSO
      // button leads nowhere.
      const user = userEvent.setup();
      const { saveConfig } = renderCard();
      await user.click(await screen.findByRole("checkbox", { name: "Enable SSO login" }));

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      expect(saveConfig).not.toHaveBeenCalled();
    });

    it("says which fields are missing", async () => {
      const user = userEvent.setup();
      renderCard();
      await user.click(await screen.findByRole("checkbox", { name: "Enable SSO login" }));

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      expect(
        await screen.findByText("Issuer URL and client ID are required before enabling SSO."),
      ).toBeInTheDocument();
    });

    it("saves once both are given", async () => {
      const user = userEvent.setup();
      const { saveConfig } = renderCard({
        oidc_issuer_url: "https://auth.test/o/printstash",
        oidc_client_id: "printstash",
      });
      await user.click(await screen.findByRole("checkbox", { name: "Enable SSO login" }));

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      await waitFor(() =>
        expect(saveConfig).toHaveBeenCalledWith(expect.objectContaining({ oidc_enabled: true })),
      );
    });

    it("lets a disabled configuration be saved incomplete", async () => {
      // Half-entered settings are worth keeping while SSO is off; the check is
      // about turning it on, not about typing.
      const user = userEvent.setup();
      const { saveConfig } = renderCard();
      await screen.findByRole("checkbox", { name: "Enable SSO login" });

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      await waitFor(() => expect(saveConfig).toHaveBeenCalled());
    });
  });

  describe("the client secret", () => {
    it("does not send one that was never typed", async () => {
      // It is never returned, so an empty field means "keep what is stored".
      const user = userEvent.setup();
      const { saveConfig } = renderCard({ has_oidc_client_secret: true });
      await screen.findByRole("checkbox", { name: "Enable SSO login" });

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      await waitFor(() =>
        expect(saveConfig.mock.calls.at(-1)?.[0]).not.toHaveProperty("oidc_client_secret"),
      );
    });

    it("sends one that was typed", async () => {
      const user = userEvent.setup();
      const { saveConfig } = renderCard();
      await user.type(await screen.findByLabelText("Client secret"), "not-a-real-secret");

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      await waitFor(() =>
        expect(saveConfig).toHaveBeenCalledWith(
          expect.objectContaining({ oidc_client_secret: "not-a-real-secret" }),
        ),
      );
    });

    it("clears the stored one only when asked explicitly", async () => {
      // "Leave blank to keep" and "blank means remove" cannot both be true of
      // one empty field, so removal is its own control.
      const user = userEvent.setup();
      const { saveConfig } = renderCard({ has_oidc_client_secret: true });
      await user.click(await screen.findByLabelText("Clear stored client secret"));

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      await waitFor(() =>
        expect(saveConfig).toHaveBeenCalledWith(
          expect.objectContaining({ oidc_client_secret: "" }),
        ),
      );
    });
  });

  describe("saving", () => {
    it("confirms the settings landed", async () => {
      const user = userEvent.setup();
      renderCard();
      await screen.findByRole("checkbox", { name: "Enable SSO login" });

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      expect(await screen.findByText("Single sign-on settings saved.")).toBeInTheDocument();
    });

    it("surfaces a configuration the server refused", async () => {
      const user = userEvent.setup();
      const config = aConfig();
      renderApp(<OidcSettingsCard />, {
        routes: {
          "GET /api/v1/config": json(config),
          "PUT /api/v1/config": json({ detail: "issuer_unreachable" }, 503),
        },
      });
      await screen.findByRole("checkbox", { name: "Enable SSO login" });

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      expect(await screen.findByText("Issuer unreachable.")).toBeInTheDocument();
    });
  });
});

describe("OIDC remote ownership recovery", () => {
  it("keeps unavailable configuration out of a writable empty form", async () => {
    const app = renderApp(<OidcSettingsCard />, {
      routes: { "GET /api/v1/config": json({ detail: "config_unavailable" }, 503) },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Configuration could not be loaded.",
    );
    expect(screen.queryByRole("button", { name: /Save SSO settings/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });
  it("holds advanced configuration controls while its first read is pending", async () => {
    const read = Promise.withResolvers<Response>();
    renderApp(<OidcSettingsCard />, { routes: { "GET /api/v1/config": () => read.promise } });
    expect(screen.queryByLabelText("Scopes")).toBeNull();
    expect(screen.queryByLabelText("Admin groups")).toBeNull();
    await act(async () => {
      read.resolve(json(aConfig()));
    });
    expect(await screen.findByLabelText("Scopes")).toBeEnabled();
  });
  it("preserves a dirty OIDC draft through a background configuration replacement", async () => {
    const base = aConfig({ oidc_issuer_url: "https://base.example.test" });
    const app = renderApp(<OidcSettingsCard />, { routes: { "GET /api/v1/config": json(base) } });
    const issuer = await screen.findByDisplayValue(base.oidc_issuer_url);
    await userEvent.clear(issuer);
    await userEvent.type(issuer, "https://draft.example.test");
    app.route({
      "GET /api/v1/config": json({ ...base, oidc_issuer_url: "https://remote.example.test" }),
    });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["vault-config"] });
    });
    expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://draft.example.test");
    expect(app.client.getQueryData(["vault-config"])).toMatchObject({
      oidc_issuer_url: "https://remote.example.test",
    });
  });
});

describe("OIDC draft lifetime", () => {
  it("keeps a failed-refresh draft read-only until explicit recovery", async () => {
    const app = renderCard({ oidc_issuer_url: "https://base.example.test" });
    const issuer = await screen.findByLabelText("Issuer URL");
    await userEvent.clear(issuer);
    await userEvent.type(issuer, "https://draft.example.test");
    app.route({ "GET /api/v1/config": json({ detail: "unavailable" }, 503) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["vault-config"] });
    });
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://draft.example.test");
    expect(screen.getByLabelText("Issuer URL")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Save SSO settings/ })).toBeDisabled();
    app.route({
      "GET /api/v1/config": json(aConfig({ oidc_issuer_url: "https://latest.example.test" })),
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByLabelText("Issuer URL")).toBeEnabled());
    expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://draft.example.test");
  });
  it("hides a denied cached configuration", async () => {
    const app = renderCard({ oidc_issuer_url: "https://private.example.test" });
    await screen.findByDisplayValue("https://private.example.test");
    app.route({ "GET /api/v1/config": json({ detail: "forbidden" }, 403) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["vault-config"] });
    });
    await screen.findByRole("alert");
    expect(screen.queryByLabelText("Issuer URL")).toBeNull();
    expect(screen.queryByRole("button", { name: /Save SSO settings/ })).toBeNull();
    expect(screen.getByRole("alert")).toBeVisible();
  });
  it("discards typed credentials on private retirement", async () => {
    renderCard();
    const secret = await screen.findByLabelText("Client secret");
    await userEvent.type(secret, "FakeRetiredSecret");
    await act(async () => {
      clearLogin();
    });
    expect(screen.queryByDisplayValue("FakeRetiredSecret")).toBeNull();
    expect(screen.queryByLabelText("Client secret")).toBeNull();
  });
  it("reflects a new saved configuration while the editor is clean", async () => {
    const app = renderCard({ oidc_issuer_url: "https://base.example.test" });
    await screen.findByDisplayValue("https://base.example.test");
    app.route({
      "GET /api/v1/config": json(aConfig({ oidc_issuer_url: "https://latest.example.test" })),
    });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["vault-config"] });
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Issuer URL")).toHaveValue("https://latest.example.test"),
    );
  });
});

describe("OIDC acknowledgement intent", () => {
  it("uses the acknowledged saved fields without another configuration read", async () => {
    const app = renderCard({ oidc_issuer_url: "https://base.example.test" });
    app.saveConfig.mockResolvedValue(
      aConfig({ oidc_issuer_url: "https://normalized.example.test" }),
    );
    const issuer = await screen.findByLabelText("Issuer URL");
    await userEvent.clear(issuer);
    await userEvent.type(issuer, "https://sent.example.test");
    await userEvent.click(screen.getByRole("button", { name: /Save SSO settings/ }));
    await waitFor(() => expect(issuer).toHaveValue("https://normalized.example.test"));
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("does not let a disposed save erase a new editor secret", async () => {
    const old = Promise.withResolvers<Response>();
    const app = renderApp(<OidcSettingsCard />, {
      routes: { "GET /api/v1/config": json(aConfig()), "PUT /api/v1/config": () => old.promise },
    });
    await userEvent.type(await screen.findByLabelText("Client secret"), "FakeOldSecret");
    await userEvent.click(screen.getByRole("button", { name: /Save SSO settings/ }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    app.unmount();
    const fresh = renderApp(<OidcSettingsCard />, {
      routes: { "GET /api/v1/config": json(aConfig({ oidc_display_name: "New editor" })) },
    });
    await userEvent.type(await screen.findByLabelText("Client secret"), "FakeNewSecret");
    await act(async () => {
      old.resolve(json(aConfig({ oidc_display_name: "Retired acknowledgement" })));
      await old.promise;
    });
    expect(screen.getByLabelText("Client secret")).toHaveValue("FakeNewSecret");
    expect(screen.queryByText("Single sign-on settings saved.")).toBeNull();
    expect(fresh.client.getQueryData(["vault-config"])).toMatchObject({
      oidc_display_name: "New editor",
    });
  });
});
