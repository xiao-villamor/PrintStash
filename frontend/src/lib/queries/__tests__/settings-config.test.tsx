/** Config commands publish sanitized authoritative DTOs without retaining credentials. */
import { useQuery } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { vaultConfigOptions, useVaultConfigCommand } from "@/lib/queries/settings-config";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
import { aVaultConfig } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { EditingBase } from "@/types/editing";
import { updateVaultConfig } from "@/lib/api/config";
function Editor({
  writer,
  base = aVaultConfig(),
}: { writer?: typeof updateVaultConfig; base?: EditingBase } = {}) {
  const query = useQuery({ ...vaultConfigOptions(), retry: false });
  const command = useVaultConfigCommand(writer);
  return (
    <>
      <p>{query.data?.oidc_display_name}</p>
      <p>{command.isPending ? "Pending" : command.isError ? "Failed" : "Ready"}</p>
      <button
        disabled={command.isPending}
        onClick={() =>
          void command
            .mutateAsync({
              session: getSessionVersion(),
              base,
              payload: { oidc_client_secret: "FakePrivateSecret", oidc_display_name: "Gesture" },
            })
            .catch(() => {})
        }
      >
        Save
      </button>
    </>
  );
}
afterEach(() => vi.restoreAllMocks());
describe("Vault configuration owner", () => {
  it("forwards a captured configuration base to an injected writer", async () => {
    const base = { edit_epoch: "a".repeat(32), edit_version: 4 };
    const writer = vi
      .fn<typeof updateVaultConfig>()
      .mockResolvedValue(aVaultConfig({ ...base, edit_version: 5, oidc_display_name: "Accepted" }));
    const app = renderApp(<Editor writer={writer} base={base} />, {
      routes: { "GET /api/v1/config": json(aVaultConfig({ ...base, oidc_display_name: "Base" })) },
    });
    await screen.findByText("Base");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Accepted")).toBeVisible();
    expect(writer).toHaveBeenCalledWith(
      expect.any(Object),
      expect.objectContaining({ base, signal: expect.any(AbortSignal) }),
    );
    expect(app.client.getMutationCache().getAll()).toHaveLength(0);
  });
  it("retains a newer observed configuration after a late receipt", async () => {
    const base = aVaultConfig({ edit_version: 1, oidc_display_name: "Base" });
    const pending = Promise.withResolvers<ReturnType<typeof aVaultConfig>>();
    const writer = vi.fn<typeof updateVaultConfig>().mockReturnValue(pending.promise);
    const app = renderApp(<Editor writer={writer} base={base} />, {
      routes: { "GET /api/v1/config": json(base) },
    });
    await screen.findByText("Base");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Pending");
    await act(async () => {
      app.client.setQueryData(
        ["vault-config"],
        aVaultConfig({ edit_version: 3, oidc_display_name: "Newer" }),
      );
    });
    await act(async () => {
      pending.resolve(aVaultConfig({ edit_version: 2, oidc_display_name: "Older" }));
    });
    expect(await screen.findByText("Ready")).toBeVisible();
    expect(screen.getByText("Newer")).toBeVisible();
    expect(app.client.getQueryData(["vault-config"])).toMatchObject({ edit_version: 3 });
  });
  it("rejects a nonadvancing injected configuration receipt", async () => {
    const base = aVaultConfig({ oidc_display_name: "Base" });
    const writer = vi
      .fn<typeof updateVaultConfig>()
      .mockResolvedValue(aVaultConfig({ oidc_display_name: "Unconfirmed" }));
    const app = renderApp(<Editor writer={writer} base={base} />, {
      routes: { "GET /api/v1/config": json(base) },
    });
    await screen.findByText("Base");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Failed")).toBeVisible();
    expect(app.client.getQueryData(["vault-config"])).toEqual(base);
  });
  it("propagates a conflict without retrying the write", async () => {
    const base = aVaultConfig({ oidc_display_name: "Base" });
    const app = renderApp(<Editor base={base} />, {
      routes: {
        "GET /api/v1/config": json(base),
        "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
      },
    });
    await screen.findByText("Base");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Failed")).toBeVisible();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    expect(app.client.getQueryData(["vault-config"])).toEqual(base);
  });
  it("publishes the injected concrete writer full DTO", async () => {
    const writer = vi
      .fn<typeof updateVaultConfig>()
      .mockResolvedValue(
        aVaultConfig({ edit_version: 2, oidc_display_name: "Injected", currency: "EUR" }),
      );
    const app = renderApp(<Editor writer={writer} />, {
      routes: { "GET /api/v1/config": json(aVaultConfig({ oidc_display_name: "Base" })) },
    });
    await screen.findByText("Base");

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Injected")).toBeVisible();
    expect(app.client.getQueryData(["vault-config"])).toMatchObject({ currency: "EUR" });
    expect(writer).toHaveBeenCalledWith(
      { oidc_client_secret: "FakePrivateSecret", oidc_display_name: "Gesture" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(app.client.getMutationCache().getAll()).toHaveLength(0);
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });

  it("publishes the complete authoritative configuration without another GET", async () => {
    const app = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/config": json(aVaultConfig({ oidc_display_name: "Base" })),
        "PUT /api/v1/config": json(
          aVaultConfig({ edit_version: 2, oidc_display_name: "Server", currency: "EUR" }),
        ),
      },
    });
    await screen.findByText("Base");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Server")).toBeVisible();
    expect(app.client.getQueryData(["vault-config"])).toMatchObject({ currency: "EUR" });
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toEqual({
      oidc_client_secret: "FakePrivateSecret",
      oidc_display_name: "Gesture",
    });
  });
  it.each([200, 503])(
    "keeps a credential outside shared caches through response %s",
    async (status) => {
      const response = Promise.withResolvers<Response>();
      const app = renderApp(<Editor />, {
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ oidc_display_name: "Base" })),
          "PUT /api/v1/config": () => response.promise,
        },
      });
      await screen.findByText("Base");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText("Pending");
      function expectPrivate() {
        expect(JSON.stringify(app.client.getMutationCache().getAll())).not.toContain(
          "FakePrivateSecret",
        );
        expect(
          JSON.stringify(
            app.client
              .getQueryCache()
              .getAll()
              .map((query) => query.state.data),
          ),
        ).not.toContain("FakePrivateSecret");
      }
      expectPrivate();
      await act(async () => {
        response.resolve(
          status === 200
            ? json(aVaultConfig({ edit_version: 2, oidc_display_name: "Saved" }))
            : json({ detail: "unavailable" }, status),
        );
      });
      await screen.findByText(status === 200 ? "Saved" : "Failed");
      expectPrivate();
      expect(app.client.getMutationCache().getAll()).toHaveLength(0);
    },
  );
  it("never dispatches a retired configuration gesture", async () => {
    const app = renderApp(<Editor />, {
      routes: { "GET /api/v1/config": json(aVaultConfig({ oidc_display_name: "Base" })) },
    });
    await screen.findByText("Base");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    expect(app.client.getQueryData(["vault-config"])).toBeUndefined();
  });
  it("does not dispatch a disposed configuration command after held cancellation", async () => {
    const app = renderApp(<Editor />, {
      routes: { "GET /api/v1/config": json(aVaultConfig({ oidc_display_name: "Base" })) },
    });
    await screen.findByText("Base");
    const paused = Promise.withResolvers<void>();
    vi.spyOn(app.client, "cancelQueries").mockImplementationOnce(() => paused.promise);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    app.unmount();
    await act(async () => {
      paused.resolve();
      await paused.promise;
    });
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });
  it("leaves a new same-user configuration read active after a retired acknowledgement", async () => {
    const app = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/config": json(aVaultConfig({ oidc_display_name: "Base" })),
        "PUT /api/v1/config": json(aVaultConfig({ edit_version: 2, oidc_display_name: "Old" })),
      },
    });
    await screen.findByText("Base");
    const cancel = app.client.cancelQueries.bind(app.client);
    const pause = Promise.withResolvers<void>();
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => pause.promise);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    app.unmount();
    clearLogin();
    const fresh = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Editor />, {
      routes: {
        "GET /api/v1/config": (_url, init) => {
          signal = init?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => {
      pause.resolve();
      await pause.promise;
    });
    expect(signal?.aborted).toBe(false);
    expect(app.client.getQueryData(["vault-config"])).toBeUndefined();
    await act(async () => {
      fresh.resolve(json(aVaultConfig({ oidc_display_name: "Current" })));
    });
    expect(await screen.findByText("Current")).toBeVisible();
  });
});
