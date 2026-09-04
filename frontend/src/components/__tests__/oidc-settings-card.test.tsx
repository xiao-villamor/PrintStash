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
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OidcSettingsCard } from "@/components/oidc-settings-card";
import { renderApp } from "@/test-support/render";
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

function aConfig(over: Partial<OidcConfig> = {}): OidcConfig {
  return {
    oidc_enabled: false,
    oidc_issuer_url: "",
    oidc_client_id: "",
    has_oidc_client_secret: false,
    oidc_scopes: "openid profile email",
    oidc_username_claim: "preferred_username",
    oidc_groups_claim: "groups",
    oidc_admin_groups: "",
    oidc_display_name: "",
    oidc_redirect_uri: "",
    oidc_allow_insecure_http: false,
    ...over,
  };
}

function renderCard(over: Partial<OidcConfig> = {}) {
  const config = aConfig(over);
  const saveConfig = vi
    .fn<(payload: VaultConfigUpdate) => Promise<OidcConfig>>()
    .mockResolvedValue(config);
  const result = renderApp(
    <OidcSettingsCard loadConfig={async () => config} saveConfig={saveConfig} />,
  );
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
      renderApp(
        <OidcSettingsCard
          loadConfig={async () => config}
          saveConfig={vi
            .fn<(payload: VaultConfigUpdate) => Promise<OidcConfig>>()
            .mockRejectedValue(new Error("issuer_unreachable"))}
        />,
      );
      await screen.findByRole("checkbox", { name: "Enable SSO login" });

      await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

      expect(await screen.findByText("Issuer unreachable.")).toBeInTheDocument();
    });
  });
});
