/** Storage connection commands publish sanitized DTOs while credential arguments remain local. */
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as storageApi from "@/lib/api/storage-connections";
import {
  storageConnectionsOptions,
  storageProvidersOptions,
  useStorageConnectionCommand,
} from "@/lib/queries/settings-storage";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
import { aStorageConnection } from "@/test-support/factories";
import { storageProviderCatalogue } from "@/test-support/storage-provider-catalogue";
import { json, renderApp } from "@/test-support/render";
type Mode = "create" | "update" | "delete" | "probe";
function Editor({ mode = "update", session: stamped }: { mode?: Mode; session?: number }) {
  const rows = useQuery(storageConnectionsOptions());
  const providers = useQuery(storageProvidersOptions());
  const command = useStorageConnectionCommand();
  function save() {
    const session = stamped ?? getSessionVersion();
    const gesture =
      mode === "create"
        ? {
            session,
            kind: mode,
            payload: {
              name: "Gesture",
              kind: "s3" as const,
              configuration: { bucket: "printstash" },
              secrets: { secret_key: "FakeOwnedStorageSecret" },
            },
          }
        : mode === "update"
          ? {
              session,
              kind: mode,
              id: 1,
              payload: { name: "Gesture", secrets: { secret_key: "FakeOwnedStorageSecret" } },
            }
          : { session, kind: mode, id: 1 };
    void command.mutateAsync(gesture).catch(() => {});
  }
  return (
    <>
      <p>{rows.data?.map((row) => `${row.name}/${row.purpose}/${row.enabled}`).join(",")}</p>
      <p>{providers.data?.length}</p>
      <p>{command.isPending ? "Pending" : command.error ? "Failed" : "Ready"}</p>
      <button disabled={command.isPending} onClick={save}>
        Save
      </button>
    </>
  );
}
const readRoutes = {
  "GET /api/v1/storage-connections": json([aStorageConnection()]),
  "GET /api/v1/storage/providers": json(storageProviderCatalogue),
};
afterEach(() => vi.restoreAllMocks());
describe("Storage connection owner", () => {
  it.each(["create", "update"] as const)(
    "publishes authoritative %s rows without another GET",
    async (mode) => {
      const saved = aStorageConnection({
        id: mode === "create" ? 2 : 1,
        name: "Server",
        purpose: "backup",
        enabled: false,
      });
      const path =
        mode === "create"
          ? "POST /api/v1/storage-connections"
          : "PATCH /api/v1/storage-connections/1";
      const app = renderApp(<Editor mode={mode} />, {
        routes: { ...readRoutes, [path]: json(saved, mode === "create" ? 201 : 200) },
      });
      await screen.findByText("Workshop storage/both/true");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(screen.getByText(/Server\/backup\/false/)).toBeVisible());
      expect(app.requestsWithMethod("GET")).toHaveLength(2);
      expect(app.client.getQueryData(["storage-connections"])).toContainEqual(saved);
    },
  );
  it("removes an acknowledged connection without another GET", async () => {
    const app = renderApp(<Editor mode="delete" />, {
      routes: {
        ...readRoutes,
        "DELETE /api/v1/storage-connections/1": new Response(null, { status: 204 }),
      },
    });
    await screen.findByText("Workshop storage/both/true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.queryByText("Workshop storage/both/true")).not.toBeInTheDocument(),
    );
    expect(app.client.getQueryData(["storage-connections"])).toEqual([]);
    expect(app.requestsWithMethod("GET")).toHaveLength(2);
  });
  it.each(["create", "update"] as const)(
    "keeps successful %s credentials outside shared caches",
    async (mode) => expect(await checkSecret(mode, 200)).toHaveLength(0),
  );
  it.each(["create", "update"] as const)(
    "keeps failed %s credentials outside shared caches",
    async (mode) => expect(await checkSecret(mode, 503)).toHaveLength(0),
  );
  async function checkSecret(mode: "create" | "update", status: number) {
    const response = Promise.withResolvers<Response>();
    const path =
      mode === "create"
        ? "POST /api/v1/storage-connections"
        : "PATCH /api/v1/storage-connections/1";
    const app = renderApp(<Editor mode={mode} />, {
      routes: { ...readRoutes, [path]: () => response.promise },
    });
    await screen.findByText("Workshop storage/both/true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Pending");
    function privateCaches() {
      expect(JSON.stringify(app.client.getMutationCache().getAll())).not.toContain(
        "FakeOwnedStorageSecret",
      );
      expect(
        JSON.stringify(
          app.client
            .getQueryCache()
            .getAll()
            .map((query) => query.state.data),
        ),
      ).not.toContain("FakeOwnedStorageSecret");
    }
    privateCaches();
    await act(async () =>
      response.resolve(
        status === 200
          ? json(aStorageConnection({ name: "Saved" }))
          : json({ detail: "unavailable" }, status),
      ),
    );
    await screen.findByText(status === 200 ? "Saved/both/true" : "Failed");
    privateCaches();
    expect(app.requestsWithMethod(mode === "create" ? "POST" : "PATCH")).toHaveLength(1);
    return app.client.getMutationCache().getAll();
  }
  it("shares canonical storage reads", async () => {
    const app = renderApp(
      <>
        <Editor />
        <Editor />
      </>,
      { routes: readRoutes },
    );
    await screen.findAllByText("Workshop storage/both/true");
    expect(app.requestsWithMethod("GET")).toHaveLength(2);
  });
  it.each(["create", "update", "delete", "probe"] as const)(
    "never dispatches a retired %s gesture",
    async (mode) => {
      const captured = getSessionVersion();
      clearLogin();
      const app = renderApp(<Editor mode={mode} session={captured} />, { routes: readRoutes });
      await screen.findByText("Workshop storage/both/true");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(
        app.requestsWithMethod(mode === "delete" ? "DELETE" : mode === "update" ? "PATCH" : "POST"),
      ).toHaveLength(0);
      expect(app.client.getQueryData(["storage-connections"])).toEqual([aStorageConnection()]);
    },
  );
  it("leaves a new same-user connection read active after a retired ACK", async () => {
    const received = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const update = storageApi.updateStorageConnection;
    vi.spyOn(storageApi, "updateStorageConnection").mockImplementation(async (...args) => {
      const row = await update(...args);
      received.resolve();
      await release.promise;
      return row;
    });
    const app = renderApp(<Editor />, {
      routes: {
        ...readRoutes,
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection({ name: "Old" })),
      },
    });
    await screen.findByText("Workshop storage/both/true");
    const cancel = vi.spyOn(app.client, "cancelQueries");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await received.promise;
    app.unmount();
    clearLogin();
    const fresh = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Editor />, {
      routes: {
        ...readRoutes,
        "GET /api/v1/storage-connections": (_url, init) => {
          signal = init?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => release.resolve());
    expect(signal?.aborted).toBe(false);
    expect(cancel).toHaveBeenCalledTimes(1);
    await act(async () => fresh.resolve(json([aStorageConnection({ name: "Current" })])));
    expect(await screen.findByText("Current/both/true")).toBeVisible();
  });
  it("does not dispatch a disposed command after held cancellation", async () => {
    const app = renderApp(<Editor />, { routes: readRoutes });
    await screen.findByText("Workshop storage/both/true");
    const paused = Promise.withResolvers<void>();
    vi.spyOn(app.client, "cancelQueries").mockImplementationOnce(() => paused.promise);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    app.unmount();
    await act(async () => paused.resolve());
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
  });
});

describe("Storage publication after cancellation", () => {
  it("discards a connection acknowledgement when retirement occurs during cancellation", async () => {
    const app = renderApp(<Editor />, {
      routes: {
        ...readRoutes,
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection({ name: "Old" })),
      },
    });
    await screen.findByText("Workshop storage/both/true");
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
        ...readRoutes,
        "GET /api/v1/storage-connections": (_url, init) => {
          signal = init?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => pause.resolve());
    expect(signal?.aborted).toBe(false);
    expect(app.client.getQueryData(["storage-connections"])).toBeUndefined();
    await act(async () => fresh.resolve(json([aStorageConnection({ name: "Current" })])));
    expect(await screen.findByText("Current/both/true")).toBeVisible();
  });
});
