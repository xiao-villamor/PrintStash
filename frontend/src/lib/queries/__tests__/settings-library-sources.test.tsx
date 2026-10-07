/** Library source receipts publish only in their initiating live private scope; scans remain durable Jobs. */
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  librarySourcesOptions,
  librarySourceKeys,
  defaultLibrarySourcesApi,
  useLibrarySourceCommand,
  type LibrarySourcesApi,
} from "@/lib/queries/settings-library-sources";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
import * as tasks from "@/lib/task-center";
import { anExternalLibrary, aJob, aStorageConnection } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
const source = anExternalLibrary({ id: 7, name: "Base", root_path: "/mnt/base" });
function Reader() {
  const query = useQuery(librarySourcesOptions());
  return (
    <p>
      {query.isError
        ? "Read failed"
        : query.data?.kind === "disabled"
          ? "Disabled"
          : query.data?.items.map((row) => row.name).join(",")}
    </p>
  );
}
function Editor({
  api = defaultLibrarySourcesApi,
  stamped,
  payload = { name: "Gesture", root_path: "/mnt/gesture" },
}: {
  api?: LibrarySourcesApi;
  stamped?: number;
  payload?: Parameters<LibrarySourcesApi["create"]>[0];
}) {
  const query = useQuery(librarySourcesOptions());
  const command = useLibrarySourceCommand(api);
  const session = () => stamped ?? getSessionVersion();
  return (
    <>
      <p>
        {query.data?.kind === "enabled"
          ? query.data.items
              .map((row) => row.name + ":" + row.enabled + ":" + row.binding_state)
              .join(",")
          : "Loading"}
      </p>
      <p>{command.busyId !== null ? "Pending" : command.error ? "Failed" : "Ready"}</p>
      <button
        onClick={() =>
          void command
            .mutateAsync({
              kind: "create",
              session: session(),
              payload,
            })
            .catch(() => {})
        }
      >
        Create
      </button>
      <button
        onClick={() =>
          void command
            .mutateAsync({ kind: "update", session: session(), id: 7, payload: { enabled: false } })
            .catch(() => {})
        }
      >
        Pause
      </button>
      <button
        onClick={() =>
          void command
            .mutateAsync({ kind: "enroll", session: session(), id: 7, root: "/mnt/base" })
            .catch(() => {})
        }
      >
        Enroll
      </button>
      <button
        onClick={() =>
          void command.mutateAsync({ kind: "delete", session: session(), id: 7 }).catch(() => {})
        }
      >
        Remove
      </button>
      <button
        onClick={() =>
          void command
            .mutateAsync({ kind: "scan", session: session(), id: 7, title: "Source scan" })
            .catch(() => {})
        }
      >
        Scan
      </button>
    </>
  );
}
const reads = { "GET /api/v1/libraries": json([source]) };
function heldScan() {
  const entered = Promise.withResolvers<void>();
  const release = Promise.withResolvers<void>();
  const api = {
    ...defaultLibrarySourcesApi,
    scan: async (...args: Parameters<LibrarySourcesApi["scan"]>) => {
      const accepted = await defaultLibrarySourcesApi.scan(...args);
      entered.resolve();
      await release.promise;
      return accepted;
    },
  };
  const app = renderApp(<Editor api={api} />, {
    routes: {
      ...reads,
      "POST /api/v1/libraries/7/scan": json(
        { job_id: "scan-lifetime", state: "queued", message: "queued" },
        202,
      ),
      "GET /api/v1/jobs": json([
        aJob({ job_id: "scan-lifetime", kind: "sources.scan", model_id: null, file_id: null }),
      ]),
    },
  });
  return { app, entered, release };
}
afterEach(() => {
  vi.restoreAllMocks();
  tasks.resetTasksForNewSetup();
});
describe("librarySourcesOptions", () => {
  it("shares one canonical source projection", async () => {
    const app = renderApp(
      <>
        <Reader />
        <Reader />
      </>,
      { routes: reads },
    );
    await screen.findAllByText("Base");
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
    expect(app.client.getQueryCache().findAll({ queryKey: librarySourceKeys.all })).toHaveLength(1);
  });
  it("classifies explicit feature opt-out as disabled", async () => {
    renderApp(<Reader />, {
      routes: { "GET /api/v1/libraries": json({ detail: "feature_disabled" }, 404) },
    });
    expect(await screen.findByText("Disabled")).toBeVisible();
  });
  it.each([
    { label: "forbidden", status: 403, code: "forbidden" },
    { label: "missing", status: 404, code: "library_not_found" },
    { label: "unavailable", status: 503, code: "unavailable" },
  ])("retains $label as a read failure", async ({ status, code }) => {
    renderApp(<Reader />, { routes: { "GET /api/v1/libraries": json({ detail: code }, status) } });
    expect(await screen.findByText("Read failed")).toBeVisible();
    expect(screen.queryByText("Disabled")).toBeNull();
  });
});
describe("useLibrarySourceCommand", () => {
  it("publishes the complete created DTO without another GET", async () => {
    const saved = anExternalLibrary({
      id: 8,
      name: "Server",
      root_path: "/mnt/server",
      enabled: false,
    });
    const app = renderApp(<Editor />, {
      routes: { ...reads, "POST /api/v1/libraries": json(saved, 201) },
    });
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Base:true:bound,Server:false:bound");
    expect(app.client.getQueryData(librarySourceKeys.all)).toEqual({
      kind: "enabled",
      items: [source, saved],
    });
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toEqual({
      name: "Gesture",
      root_path: "/mnt/gesture",
    });
  });
  it("publishes the returned update without another GET", async () => {
    const app = renderApp(<Editor />, {
      routes: { ...reads, "PATCH /api/v1/libraries/7": json({ ...source, enabled: false }) },
    });
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(await screen.findByText("Base:false:bound")).toBeVisible();
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("publishes exact root enrollment without another GET", async () => {
    const app = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/libraries": json([
          { ...source, binding_state: "missing", root_enrollable: true },
        ]),
        "POST /api/v1/libraries/7/root/enroll": json(source),
      },
    });
    await screen.findByText("Base:true:missing");
    await userEvent.click(screen.getByRole("button", { name: "Enroll" }));
    expect(await screen.findByText("Base:true:bound")).toBeVisible();
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toEqual({
      confirm_root_path: "/mnt/base",
    });
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("removes only the confirmed row on204", async () => {
    const other = anExternalLibrary({ id: 8, name: "Other" });
    const app = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/libraries": json([source, other]),
        "DELETE /api/v1/libraries/7": json(null, 204),
      },
    });
    await screen.findByText("Base:true:bound,Other:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByText("Other:true:bound")).toBeVisible();
    expect(app.requestsWithMethod("DELETE")).toEqual([
      { method: "DELETE", url: "/api/v1/libraries/7", body: "" },
    ]);
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("refuses a retired gesture before dispatch", async () => {
    const stamped = getSessionVersion();
    clearLogin();
    const app = renderApp(<Editor stamped={stamped} />, { routes: reads });
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.client.getQueryData(librarySourceKeys.all)).toEqual({
      kind: "enabled",
      items: [source],
    });
  });
  it("revalidates remote connection membership after cancellation", async () => {
    const payload = {
      name: "Remote gesture",
      source_kind: "s3" as const,
      connection_id: 4,
      source_prefix: "models",
    };
    const app = renderApp(<Editor payload={payload} />, {
      routes: reads,
      seed: [
        [["storage-connections"], [aStorageConnection({ id: 4, kind: "s3", purpose: "library" })]],
      ],
    });
    await screen.findByText("Base:true:bound");
    const native = app.client.cancelQueries.bind(app.client);
    const release = Promise.withResolvers<void>();
    vi.spyOn(app.client, "cancelQueries").mockImplementationOnce(async (...args) => {
      await native(...args);
      await release.promise;
    });
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Pending");

    await act(async () => {
      app.client.setQueryData(["storage-connections"], []);
      release.resolve();
    });

    expect(await screen.findByText("Failed")).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("does not dispatch after retirement during initial cancellation", async () => {
    const app = renderApp(<Editor />, { routes: reads });
    await screen.findByText("Base:true:bound");
    const native = app.client.cancelQueries.bind(app.client);
    const release = Promise.withResolvers<void>();
    vi.spyOn(app.client, "cancelQueries").mockImplementationOnce(async (...args) => {
      await native(...args);
      await release.promise;
    });
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Pending");

    // The private shell disposes observers and clears Query on session retirement.
    app.unmount();
    clearLogin();
    app.client.clear();
    await act(async () => release.resolve());

    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.client.getQueryData(librarySourceKeys.all)).toBeUndefined();
  });
  it("leaves a new same-user read active after retired acknowledgement", async () => {
    const entered = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const api = {
      ...defaultLibrarySourcesApi,
      create: async (...args: Parameters<LibrarySourcesApi["create"]>) => {
        const row = await defaultLibrarySourcesApi.create(...args);
        entered.resolve();
        await release.promise;
        return row;
      },
    };
    const app = renderApp(<Editor api={api} />, {
      routes: { ...reads, "POST /api/v1/libraries": json({ ...source, name: "Retired" }, 201) },
    });
    await screen.findByText("Base:true:bound");
    const cancel = vi.spyOn(app.client, "cancelQueries");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await entered.promise;
    app.unmount();
    clearLogin();
    const current = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Reader />, {
      routes: {
        "GET /api/v1/libraries": (_url, options) => {
          signal = options?.signal;
          return current.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => release.resolve());
    expect(signal?.aborted).toBe(false);
    expect(cancel).toHaveBeenCalledTimes(1);
    await act(async () => current.resolve(json([{ ...source, name: "Current" }])));
    expect(await screen.findByText("Current")).toBeVisible();
  });
  it("does not publish after retirement during cancellation", async () => {
    const app = renderApp(<Editor />, {
      routes: { ...reads, "POST /api/v1/libraries": json({ ...source, name: "Retired" }, 201) },
    });
    await screen.findByText("Base:true:bound");
    const native = app.client.cancelQueries.bind(app.client);
    const release = Promise.withResolvers<void>();
    const cancel = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(native)
      .mockImplementationOnce(() => release.promise);
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledTimes(2));
    app.unmount();
    clearLogin();
    const fresh = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Reader />, {
      routes: {
        "GET /api/v1/libraries": (_url, options) => {
          signal = options?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => release.resolve());
    expect(signal?.aborted).toBe(false);
    expect(app.client.getQueryData(librarySourceKeys.all)).toBeUndefined();
    await act(async () => fresh.resolve(json([{ ...source, name: "Current" }])));
    expect(await screen.findByText("Current")).toBeVisible();
  });
  it("suppresses a disposed acknowledgement", async () => {
    const receipt = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const api = {
      ...defaultLibrarySourcesApi,
      create: async (...args: Parameters<LibrarySourcesApi["create"]>) => {
        const row = await defaultLibrarySourcesApi.create(...args);
        receipt.resolve();
        await release.promise;
        return row;
      },
    };
    const app = renderApp(<Editor api={api} />, {
      routes: { ...reads, "POST /api/v1/libraries": json({ ...source, name: "Disposed" }, 201) },
    });
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await receipt.promise;
    app.unmount();
    await act(async () => release.resolve());
    expect(app.client.getQueryData(librarySourceKeys.all)).toEqual({
      kind: "enabled",
      items: [source],
    });
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("retains current accepted scan tracking after disposal", async () => {
    const { app, entered, release } = heldScan();
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Scan" }));
    await entered.promise;

    app.unmount();
    await act(async () => release.resolve());

    await waitFor(() =>
      expect(tasks.listTasks().some((task) => task.jobId === "scan-lifetime")).toBe(true),
    );
    expect(
      app.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/libraries"),
    ).toHaveLength(1);
  });
  it("rejects retired scan acceptance", async () => {
    const { app, entered, release } = heldScan();
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Scan" }));
    await entered.promise;

    app.unmount();
    clearLogin();
    tasks.resetTasksForNewSetup();
    await act(async () => release.resolve());

    expect(tasks.listTasks().some((task) => task.jobId === "scan-lifetime")).toBe(false);
    expect(
      app.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/libraries"),
    ).toHaveLength(1);
  });
  it.each([
    { label: "completed", state: "completed" },
    { label: "failed", state: "failed" },
  ] as const)("refreshes source status once after $label scan", async ({ state }) => {
    const app = renderApp(<Editor />, {
      routes: {
        ...reads,
        "POST /api/v1/libraries/7/scan": json(
          { job_id: "scan-terminal", state: "queued", message: "queued" },
          202,
        ),
        "GET /api/v1/jobs": json([
          aJob({
            job_id: "scan-terminal",
            kind: "sources.scan",
            model_id: null,
            file_id: null,
            state,
            error: state === "failed" ? "scan_failed" : null,
          }),
        ]),
      },
    });
    await screen.findByText("Base:true:bound");
    await userEvent.click(screen.getByRole("button", { name: "Scan" }));
    await waitFor(() =>
      expect(
        app.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/libraries"),
      ).toHaveLength(2),
    );
    expect(
      app.requestsWithMethod("POST").filter((row) => row.url === "/api/v1/libraries/7/scan"),
    ).toHaveLength(1);
    expect(await screen.findByText(state === "failed" ? "Failed" : "Ready")).toBeVisible();
  });
});
