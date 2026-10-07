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
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ProviderConnectionsPanel,
  type ProviderConnectionsPanelDeps,
} from "@/components/provider-connections-panel";
import { clearLogin } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
let client: QueryClient;

function renderPanel() {
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <ProviderConnectionsPanel deps={api} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api = deps();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.mocked(api.listProviderConnections).mockResolvedValue([
    { provider: "myminifactory", connected: false, updated_at: null },
    { provider: "cults", connected: true, updated_at: "2026-08-24T01:00:00Z" },
  ]);
  vi.mocked(api.listBrowserDevices).mockResolvedValue([
    {
      id: 4,
      edit_epoch: "a".repeat(32),
      edit_version: 1,
      name: "Workshop Firefox",
      created_at: "2026-08-24T01:00:00Z",
      last_used_at: null,
      revoked_at: null,
    },
  ]);
});

afterEach(cleanup);

describe("ProviderConnectionsPanel", () => {
  it("retains the original browser base during refresh", async () => {
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "My draft");
    const rows = await api.listBrowserDevices();
    const original = rows[0]!;
    vi.mocked(api.listBrowserDevices).mockResolvedValue([
      { ...original, name: "Other editor", edit_version: 2 },
    ]);
    await act(async () => {
      await client.refetchQueries({ queryKey: ["provider-accounts", "devices"] });
    });
    vi.mocked(api.renameBrowserDevice).mockRejectedValue(
      new ApiError(412, "edit_conflict", "edit_conflict"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await waitFor(() =>
      expect(api.renameBrowserDevice).toHaveBeenCalledWith(
        4,
        { name: "My draft" },
        expect.objectContaining({ base: expect.objectContaining({ edit_version: 1 }) }),
      ),
    );
  });

  it("requires review after a browser conflict", async () => {
    vi.mocked(api.renameBrowserDevice).mockRejectedValue(
      new ApiError(412, "edit_conflict", "edit_conflict"),
    );
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "My draft");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    expect(await screen.findByRole("button", { name: "Review current values" })).toBeVisible();
    expect(input).toHaveValue("My draft");
    expect(screen.getByRole("button", { name: "Save browser name" })).toBeDisabled();
    expect(api.renameBrowserDevice).toHaveBeenCalledTimes(1);
  });

  it("saves explicitly revised browser changes", async () => {
    const [original] = await api.listBrowserDevices();
    if (!original) throw new Error("Missing browser fixture");
    vi.mocked(api.renameBrowserDevice)
      .mockRejectedValueOnce(new ApiError(412, "edit_conflict", "edit_conflict"))
      .mockImplementationOnce(async (id, { name }, { base }) => ({
        ...original,
        id,
        name,
        ...base,
        edit_version: base.edit_version + 1,
      }));
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "My draft");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await screen.findByRole("button", { name: "Review current values" });
    vi.mocked(api.listBrowserDevices).mockResolvedValue([
      { ...original, name: "Winner", edit_version: 2 },
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    expect(await screen.findByText("Current browser name: Winner")).toBeVisible();
    expect(input).toHaveValue("My draft");
    await userEvent.click(screen.getByRole("button", { name: "Save revised changes" }));
    await waitFor(() =>
      expect(api.renameBrowserDevice).toHaveBeenLastCalledWith(
        4,
        { name: "My draft" },
        expect.objectContaining({ base: expect.objectContaining({ edit_version: 2 }) }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Review current values" }),
      ).not.toBeInTheDocument(),
    );
    expect(client.getQueryData(["provider-accounts", "devices"])).toEqual([
      expect.objectContaining({ name: "My draft", edit_version: 3 }),
    ]);
  });

  it("adopts a replacement browser pairing", async () => {
    const [original] = await api.listBrowserDevices();
    if (!original) throw new Error("Missing browser fixture");
    vi.mocked(api.renameBrowserDevice).mockRejectedValue(
      new ApiError(412, "edit_conflict", "edit_conflict"),
    );
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "Old draft");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await screen.findByRole("button", { name: "Review current values" });
    vi.mocked(api.listBrowserDevices).mockResolvedValue([
      { ...original, name: "Replacement", edit_epoch: "b".repeat(32) },
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    expect(await screen.findByRole("button", { name: "Save revised changes" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Use current values" }));
    expect(input).toHaveValue("Replacement");
    expect(screen.getByRole("button", { name: "Save browser name" })).toBeDisabled();
    expect(api.renameBrowserDevice).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403, 404, 200])("retires an inaccessible browser editor: %s", async (status) => {
    vi.mocked(api.renameBrowserDevice).mockRejectedValue(
      new ApiError(412, "edit_conflict", "edit_conflict"),
    );
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "Private draft");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await screen.findByRole("button", { name: "Review current values" });
    if (status === 200) vi.mocked(api.listBrowserDevices).mockResolvedValue([]);
    else
      vi.mocked(api.listBrowserDevices).mockRejectedValue(
        new ApiError(status, "forbidden", "forbidden"),
      );
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(screen.queryByDisplayValue("Private draft")).not.toBeInTheDocument();
    expect(api.renameBrowserDevice).toHaveBeenCalledTimes(1);
  });

  it("refuses an obsolete browser receipt", async () => {
    const [original] = await api.listBrowserDevices();
    if (!original) throw new Error("Missing browser fixture");
    const pending = Promise.withResolvers<Awaited<ReturnType<typeof api.renameBrowserDevice>>>();
    vi.mocked(api.renameBrowserDevice).mockReturnValue(pending.promise);
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "Retired name");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await waitFor(() => expect(api.renameBrowserDevice).toHaveBeenCalledTimes(1));
    await act(async () => {
      clearLogin();
      pending.resolve({ ...original, name: "Retired name", edit_version: 2 });
      await pending.promise;
    });
    expect(client.getQueryData(["provider-accounts", "devices"])).toEqual([original]);
    expect(screen.queryByDisplayValue("Retired name")).not.toBeInTheDocument();
  });

  it("keeps review failure recoverable", async () => {
    vi.mocked(api.renameBrowserDevice).mockRejectedValue(new TypeError("Failed to fetch"));
    renderPanel();
    const input = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(input);
    await userEvent.type(input, "Uncertain draft");
    await userEvent.click(screen.getByRole("button", { name: "Save browser name" }));
    await screen.findByRole("button", { name: "Review current values" });
    vi.mocked(api.listBrowserDevices).mockRejectedValueOnce(
      new ApiError(503, "unavailable", "unavailable"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review current values" })).toBeEnabled(),
    );
    expect(input).toHaveValue("Uncertain draft");
    expect(screen.queryByRole("button", { name: "Save revised changes" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    expect(await screen.findByText("Current browser name: Workshop Firefox")).toBeVisible();
    expect(api.renameBrowserDevice).toHaveBeenCalledTimes(1);
  });

  it("refuses an OAuth handoff from an earlier session", async () => {
    const pending = Promise.withResolvers<{ authorization_url: string }>();
    vi.mocked(api.authorizeMyMiniFactory).mockReturnValue(pending.promise);
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Connect MyMiniFactory" }));
    await waitFor(() => expect(api.authorizeMyMiniFactory).toHaveBeenCalledTimes(1));
    await act(async () => {
      clearLogin();
      pending.resolve({ authorization_url: "https://myminifactory.test/retired" });
      await pending.promise;
    });
    expect(api.navigate).not.toHaveBeenCalled();
  });

  it("refuses a credential receipt from an earlier session", async () => {
    vi.mocked(api.listProviderConnections).mockResolvedValue([]);
    const pending = Promise.withResolvers<Awaited<ReturnType<typeof api.connectCults>>>();
    vi.mocked(api.connectCults).mockReturnValue(pending.promise);
    renderPanel();
    await userEvent.type(await screen.findByLabelText("Cults username"), "retired-user");
    await userEvent.type(screen.getByLabelText("Cults password"), "retired-password");
    await userEvent.click(screen.getByRole("button", { name: "Connect Cults" }));
    await waitFor(() => expect(api.connectCults).toHaveBeenCalledTimes(1));
    await act(async () => {
      clearLogin();
      pending.resolve({ provider: "cults", connected: true, updated_at: "2026-08-24T01:00:00Z" });
      await pending.promise;
    });
    expect(client.getQueryData(["provider-accounts", "connections"])).toEqual([]);
    expect(screen.getByLabelText("Cults username")).toHaveValue("");
    expect(screen.getByLabelText("Cults password")).toHaveValue("");
    expect(client.getMutationCache().getAll()).toEqual([]);
  });

  it("refuses a retired OAuth navigation", async () => {
    const response = Promise.withResolvers<{ authorization_url: string }>();
    vi.mocked(api.authorizeMyMiniFactory).mockReturnValue(response.promise);
    const view = renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Connect MyMiniFactory" }));
    await waitFor(() => expect(api.authorizeMyMiniFactory).toHaveBeenCalledTimes(1));
    view.unmount();
    response.resolve({ authorization_url: "https://myminifactory.test/authorize?state=retired" });
    await response.promise;
    await waitFor(() => expect(api.navigate).not.toHaveBeenCalled());
  });

  it("waits for provider authority before exposing connect actions", async () => {
    const pending =
      Promise.withResolvers<Awaited<ReturnType<typeof api.listProviderConnections>>>();
    vi.mocked(api.listProviderConnections).mockReturnValue(pending.promise);
    renderPanel();
    expect(screen.queryByRole("button", { name: "Connect MyMiniFactory" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeVisible();
    pending.resolve([]);
    expect(await screen.findByRole("button", { name: "Connect MyMiniFactory" })).toBeVisible();
  });

  it("retries failed provider reads explicitly", async () => {
    vi.mocked(api.listProviderConnections).mockRejectedValueOnce(new Error("unavailable"));
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("Not connected")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Connect MyMiniFactory" })).toBeVisible();
  });

  it("cancels provider reads after disposal", async () => {
    let signal: AbortSignal | undefined;
    const pending =
      Promise.withResolvers<Awaited<ReturnType<typeof api.listProviderConnections>>>();
    vi.mocked(api.listProviderConnections).mockImplementation((options) => {
      signal = options?.signal;
      return pending.promise;
    });
    const view = renderPanel();
    await waitFor(() => expect(signal).toBeDefined());
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      pending.resolve([]);
      await pending.promise;
    });
    expect(client.getQueryData(["provider-accounts", "connections"])).toBeUndefined();
  });

  it("blocks duplicate overlapping gestures", async () => {
    const pending = Promise.withResolvers<{ authorization_url: string }>();
    vi.mocked(api.authorizeMyMiniFactory).mockReturnValue(pending.promise);
    renderPanel();
    const connect = await screen.findByRole("button", { name: "Connect MyMiniFactory" });
    await userEvent.click(connect);
    await userEvent.click(screen.getByRole("button", { name: "Create pairing code" }));
    expect(api.createBrowserPairing).not.toHaveBeenCalled();
    expect(api.authorizeMyMiniFactory).toHaveBeenCalledTimes(1);
  });

  it("preserves a failed disconnect confirmation", async () => {
    vi.mocked(api.disconnectProvider).mockRejectedValue(new Error("failed"));
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(dialog).toBeVisible();
    expect(within(dialog).getByText(/failed/)).toBeVisible();
    expect(screen.getByText("Connected", { exact: true })).toBeVisible();
  });

  it("preserves a failed revocation confirmation", async () => {
    vi.mocked(api.revokeBrowserDevice).mockRejectedValue(new Error("failed"));
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Revoke Workshop Firefox" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke browser" }));
    expect(await within(dialog).findByText(/failed/)).toBeVisible();
    expect(dialog).toBeVisible();
    expect(screen.getByLabelText("Browser name for Workshop Firefox")).toBeEnabled();
  });

  it("preserves a confirmed connection against an older read", async () => {
    vi.mocked(api.listProviderConnections).mockResolvedValue([]);
    vi.mocked(api.connectCults).mockResolvedValue({
      provider: "cults",
      connected: true,
      updated_at: "2026-08-24T01:00:00Z",
    });
    renderPanel();
    await userEvent.type(await screen.findByLabelText("Cults username"), "test-user");
    await userEvent.type(screen.getByLabelText("Cults password"), "test-password");
    const stale = Promise.withResolvers<Awaited<ReturnType<typeof api.listProviderConnections>>>();
    vi.mocked(api.listProviderConnections).mockReturnValue(stale.promise);
    await act(async () => {
      void client.refetchQueries({ queryKey: ["provider-accounts", "connections"] });
    });
    await userEvent.click(screen.getByRole("button", { name: "Connect Cults" }));
    await screen.findByText("Connected", { exact: true });
    await act(async () => {
      stale.resolve([]);
      await stale.promise;
    });
    expect(screen.getByText("Connected", { exact: true })).toBeVisible();
    expect(client.getQueryData(["provider-accounts", "connections"])).toEqual([
      { provider: "cults", connected: true, updated_at: "2026-08-24T01:00:00Z" },
    ]);
  });

  it("reconciles device revocation from the server", async () => {
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Revoke Workshop Firefox" }));
    const revoked = {
      id: 4,
      edit_epoch: "a".repeat(32),
      edit_version: 1,
      name: "Workshop Firefox",
      created_at: "2026-08-24T01:00:00Z",
      last_used_at: null,
      revoked_at: "2026-08-24T02:00:00Z",
    };
    vi.mocked(api.listBrowserDevices).mockResolvedValue([revoked]);
    await userEvent.click(screen.getByRole("button", { name: "Revoke browser" }));
    expect(await screen.findByText("Revoked", { exact: true })).toBeVisible();
    expect(client.getQueryData(["provider-accounts", "devices"])).toEqual([revoked]);
  });

  it("retires unauthorized private rows", async () => {
    renderPanel();
    await screen.findByLabelText("Browser name for Workshop Firefox");
    vi.mocked(api.listBrowserDevices).mockRejectedValue(
      new ApiError(403, "forbidden", "forbidden"),
    );
    await act(async () => {
      await client.refetchQueries({ queryKey: ["provider-accounts", "devices"] });
    });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByLabelText("Browser name for Workshop Firefox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create pairing code" })).not.toBeInTheDocument();
  });

  it("refuses pairing receipts from an earlier session", async () => {
    const pending = Promise.withResolvers<{ code: string; expires_at: string }>();
    vi.mocked(api.createBrowserPairing).mockReturnValue(pending.promise);
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Create pairing code" }));
    await waitFor(() => expect(api.createBrowserPairing).toHaveBeenCalledTimes(1));
    await act(async () => {
      clearLogin();
      pending.resolve({ code: "RETIRED-PAIRING", expires_at: "2026-08-24T02:00:00Z" });
      await pending.promise;
    });
    expect(screen.queryByText("RETIRED-PAIRING")).not.toBeInTheDocument();
    expect(client.getMutationCache().getAll()).toEqual([]);
  });

  it("retains browser-name input after a transient read failure", async () => {
    renderPanel();
    const name = await screen.findByLabelText("Browser name for Workshop Firefox");
    await userEvent.clear(name);
    await userEvent.type(name, "Unsaved browser name");
    vi.mocked(api.listBrowserDevices).mockRejectedValueOnce(
      new ApiError(503, "unavailable", "unavailable"),
    );
    await act(async () => {
      await client.refetchQueries({ queryKey: ["provider-accounts", "devices"] });
    });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.getByLabelText("Browser name for Workshop Firefox")).toHaveValue(
      "Unsaved browser name",
    );
    expect(screen.getByRole("button", { name: "Save browser name" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save browser name" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Browser name for Workshop Firefox")).toHaveValue(
      "Unsaved browser name",
    );
  });

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
      expect(api.connectCults).toHaveBeenCalledWith(
        {
          username: "private-user",
          password: "private-password",
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(screen.queryByLabelText("Cults username")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cults password")).not.toBeInTheDocument();
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data),
      ),
    ).not.toContain("private-password");
    expect(client.getMutationCache().getAll()).toEqual([]);
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
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data),
      ),
    ).not.toContain("ABCD-1234");
    expect(client.getMutationCache().getAll()).toEqual([]);
  });

  it("renames a device and confirms before revocation", async () => {
    vi.mocked(api.renameBrowserDevice).mockImplementation(async (id, { name }) => ({
      id,
      edit_epoch: "a".repeat(32),
      edit_version: 2,
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
      expect(api.renameBrowserDevice).toHaveBeenCalledWith(
        4,
        { name: "Office Firefox" },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Revoke Office Firefox" }));
    expect(screen.getByRole("dialog", { name: "Revoke paired browser" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Revoke browser" }));
    await waitFor(() =>
      expect(api.revokeBrowserDevice).toHaveBeenCalledWith(
        4,
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
  });
});
