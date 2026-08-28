/*
 * Connecting an account or a browser to PrintStash, without keeping the secret.
 *
 * Three credential shapes and one rule: none of them stays in the client. OAuth
 * hands off to a server-provided authorization URL, so the client never sees a
 * token. Cults takes a username and password, which are exchanged and dropped
 * rather than held in component state. Pairing shows a *temporary code*, never the
 * device credential the backend issues from it — the code is safe to read off a
 * screen; the credential is not.
 *
 * Revocation confirms and renaming does not, which is the same asymmetry as
 * everywhere else: revoking a device is what a user does after losing it, and
 * doing it by accident locks out the one they are holding.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ProviderConnectionsPanel,
  type ProviderConnectionsPanelDeps,
} from "@/components/provider-connections-panel";
import { I18nProvider } from "@/lib/i18n";

function deps(): ProviderConnectionsPanelDeps {
  return {
    listProviderConnections: vi.fn<ProviderConnectionsPanelDeps["listProviderConnections"]>(),
    authorizeMyMiniFactory: vi.fn<ProviderConnectionsPanelDeps["authorizeMyMiniFactory"]>(),
    connectCults: vi.fn<ProviderConnectionsPanelDeps["connectCults"]>(),
    disconnectProvider: vi.fn<ProviderConnectionsPanelDeps["disconnectProvider"]>(),
    createBrowserPairing: vi.fn<ProviderConnectionsPanelDeps["createBrowserPairing"]>(),
    listBrowserDevices: vi.fn<ProviderConnectionsPanelDeps["listBrowserDevices"]>(),
    renameBrowserDevice: vi.fn<ProviderConnectionsPanelDeps["renameBrowserDevice"]>(),
    revokeBrowserDevice: vi.fn<ProviderConnectionsPanelDeps["revokeBrowserDevice"]>(),
    navigate: vi.fn<ProviderConnectionsPanelDeps["navigate"]>(),
  };
}

let api = deps();

function renderPanel() {
  return render(
    <I18nProvider>
      <ProviderConnectionsPanel deps={api} />
    </I18nProvider>,
  );
}

beforeEach(() => {
  api = deps();
  vi.mocked(api.listProviderConnections).mockResolvedValue([
    { provider: "myminifactory", connected: false, updated_at: null },
    { provider: "cults", connected: true, updated_at: "2026-08-24T01:00:00Z" },
  ]);
  vi.mocked(api.listBrowserDevices).mockResolvedValue([
    {
      id: 4,
      name: "Workshop Firefox",
      created_at: "2026-08-24T01:00:00Z",
      last_used_at: null,
      revoked_at: null,
    },
  ]);
});

afterEach(cleanup);

describe("ProviderConnectionsPanel", () => {
  it("starts OAuth by navigating to the server-provided authorization URL", async () => {
    vi.mocked(api.authorizeMyMiniFactory).mockResolvedValue({
      authorization_url: "https://myminifactory.test/authorize?state=opaque",
    });
    renderPanel();

    await userEvent.click(await screen.findByRole("button", { name: "Connect MyMiniFactory" }));

    await waitFor(() =>
      expect(api.navigate).toHaveBeenCalledWith(expect.stringContaining("state=")),
    );
  });

  it("connects Cults from newly entered credentials without retaining them", async () => {
    vi.mocked(api.listProviderConnections).mockResolvedValue([
      { provider: "myminifactory", connected: false, updated_at: null },
      { provider: "cults", connected: false, updated_at: null },
    ]);
    vi.mocked(api.connectCults).mockResolvedValue({
      provider: "cults",
      connected: true,
      updated_at: "2026-08-24T01:00:00Z",
    });
    renderPanel();
    await screen.findByText("Cults");

    await userEvent.type(screen.getByLabelText("Cults username"), "private-user");
    await userEvent.type(screen.getByLabelText("Cults password"), "private-password");
    await userEvent.click(screen.getByRole("button", { name: "Connect Cults" }));

    await waitFor(() =>
      expect(api.connectCults).toHaveBeenCalledWith({
        username: "private-user",
        password: "private-password",
      }),
    );
    expect(screen.queryByLabelText("Cults username")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cults password")).not.toBeInTheDocument();
  });

  it("shows a temporary pairing code and never a device credential", async () => {
    vi.mocked(api.createBrowserPairing).mockResolvedValue({
      code: "ABCD-1234",
      expires_at: "2026-08-24T01:10:00Z",
    });
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Create pairing code" }));

    expect(await screen.findByText("ABCD-1234")).toBeInTheDocument();
    expect(screen.getByText(/expires at/i)).toBeInTheDocument();
    expect(screen.queryByText(/psk_/i)).not.toBeInTheDocument();
  });

  it("renames a device and confirms before revocation", async () => {
    vi.mocked(api.renameBrowserDevice).mockImplementation(async (id, { name }) => ({
      id,
      name,
      created_at: "2026-08-24T01:00:00Z",
      last_used_at: null,
      revoked_at: null,
    }));
    renderPanel();
    const name = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(name);
    await userEvent.type(name, "Office Firefox");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await waitFor(() =>
      expect(api.renameBrowserDevice).toHaveBeenCalledWith(4, { name: "Office Firefox" }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Revoke Office Firefox" }));
    expect(screen.getByRole("dialog", { name: "Revoke paired browser" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Revoke browser" }));
    await waitFor(() => expect(api.revokeBrowserDevice).toHaveBeenCalledWith(4));
  });
});
