/**
 * The fleet API client: the print queue, batches, and per-printer maintenance.
 *
 * Almost everything here is read `fresh`, and that is the point of the module rather than
 * an incidental choice. A queue is a live thing — a job dispatches, a printer starts
 * draining, a maintenance window opens — and a cached view of it is an operator making a
 * decision from a screen that is already wrong. The one thing they might do next, hitting
 * "print", is exactly what a stale queue makes dangerous.
 *
 * The writes divide the same way the backend's permissions do: enqueuing, reordering and
 * retrying act on a **job**, while routing and maintenance act on a **printer**, so they
 * live under different paths and a slip between them would silently apply a change to the
 * wrong resource.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  checkFleetCompatibility,
  createFleetBatch,
  createMaintenanceLog,
  createMaintenanceWindow,
  decideFleetOperatorGate,
  deleteFleetJob,
  deleteMaintenanceLog,
  deleteMaintenanceWindow,
  enqueueFleetJob,
  getFleetSummary,
  listFleetQueue,
  listMaintenanceLog,
  listMaintenanceWindows,
  retryFleetJob,
  resolveFleetJob,
  updateFleetJob,
  updatePrinterRouting,
} from "@/lib/api/fleet";
import { isLoggedIn, storeLogin } from "@/lib/auth-store";
import { queryClient } from "@/lib/query-client";
import { aMaintenanceWindow, aMaintenanceLog } from "@/test-support/factories";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getFleetSummary", () => {
  it("reads the summary", async () => {
    respondWith({ queued: 0, printing: 0 });

    await getFleetSummary();

    expectRequest("/api/v1/fleet/summary");
  });

  it("never serves a cached fleet state", async () => {
    respondWith({});

    await getFleetSummary();

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("listFleetQueue", () => {
  it("asks for a default page of history", async () => {
    respondWith([]);

    await listFleetQueue();

    expectRequest("/api/v1/fleet/queue?history_limit=20&history_offset=0");
  });

  it("asks for the page the caller wants", async () => {
    respondWith([]);

    await listFleetQueue(50, 100);

    expectRequest("/api/v1/fleet/queue?history_limit=50&history_offset=100");
  });

  it("never serves a cached queue", async () => {
    respondWith([]);

    await listFleetQueue();

    // An operator deciding what to print next from a stale queue is the failure
    // this whole module is shaped to avoid.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("enqueueFleetJob", () => {
  it("POSTs the job", async () => {
    respondWith({ id: 1 });

    await enqueueFleetJob({ file_id: 2, strategy: "least_busy" });

    expectRequest("/api/v1/fleet/queue", "POST");
    expect(lastBody()).toMatchObject({ file_id: 2 });
  });
});

describe("checkFleetCompatibility", () => {
  it("asks about one file against several printers at once", async () => {
    respondWith({ printers: [] });

    await checkFleetCompatibility(2, [3, 4]);

    // One round trip for the whole fleet: the UI renders a per-printer verdict
    // for every card at once.
    expectRequest("/api/v1/fleet/compatibility", "POST");
    expect(lastBody()).toEqual({ file_id: 2, printer_ids: [3, 4] });
  });
});

describe("createFleetBatch", () => {
  it("POSTs the batch", async () => {
    respondWith({ id: 1, jobs: [] });

    await createFleetBatch({ file_id: 2, quantity: 3, strategy: "least_busy" });

    expectRequest("/api/v1/fleet/batches", "POST");
    expect(lastBody()).toMatchObject({ quantity: 3 });
  });
});

describe("decideFleetOperatorGate", () => {
  it("releases a held job", async () => {
    respondWith({ id: 1 });

    await decideFleetOperatorGate(1, "release");

    expectRequest("/api/v1/fleet/queue/1/operator-decision", "POST");
    expect(lastBody()).toEqual({ action: "release" });
  });

  it("holds a job", async () => {
    respondWith({ id: 1 });

    await decideFleetOperatorGate(1, "hold");

    expect(lastBody()).toEqual({ action: "hold" });
  });
});

describe("updateFleetJob", () => {
  it("PATCHes only what changed", async () => {
    respondWith({ id: 1 });

    await updateFleetJob(1, { priority: "rush" });

    expectRequest("/api/v1/fleet/queue/1", "PATCH");
    expect(lastBody()).toEqual({ priority: "rush" });
  });
});

describe("deleteFleetJob", () => {
  it("DELETEs the job", async () => {
    respondWith(null, 204);

    await deleteFleetJob(1);

    expectRequest("/api/v1/fleet/queue/1", "DELETE");
  });
});

describe("retryFleetJob", () => {
  it("POSTs to the job's retry sub-resource", async () => {
    respondWith({ id: 1 });

    await retryFleetJob(1);

    expectRequest("/api/v1/fleet/queue/1/retry", "POST");
  });
});

describe("resolveFleetJob", () => {
  it("marks a stale active job failed", async () => {
    respondWith({ id: 1, state: "failed" });

    await resolveFleetJob(1, "failed");

    expectRequest("/api/v1/fleet/queue/1/resolve", "POST");
    expect(lastBody()).toEqual({ resolution: "failed" });
  });
});

describe("updatePrinterRouting", () => {
  it("PATCHes the printer, not the queue", async () => {
    respondWith({ id: 3 });

    await updatePrinterRouting(3, { drain_mode: true, drain_reason: "maintenance" });

    // Routing is a property of the machine; a slip onto the queue path would
    // silently change a job instead.
    expectRequest("/api/v1/fleet/printers/3/routing", "PATCH");
  });
});

describe("maintenance windows", () => {
  it("lists a printer's windows fresh", async () => {
    respondWith([]);

    await listMaintenanceWindows(3);

    expectRequest("/api/v1/fleet/printers/3/maintenance-windows");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("creates one", async () => {
    respondWith({ id: 1 });

    await createMaintenanceWindow(3, {
      starts_at: "2026-01-01T00:00:00Z",
      ends_at: "2026-01-02T00:00:00Z",
    });

    expectRequest("/api/v1/fleet/printers/3/maintenance-windows", "POST");
  });

  it("deletes one from its printer", async () => {
    respondWith(null, 204);

    await deleteMaintenanceWindow(3, 9);

    expectRequest("/api/v1/fleet/printers/3/maintenance-windows/9", "DELETE");
  });
});

describe("maintenance log", () => {
  it("lists a printer's log fresh", async () => {
    respondWith([]);

    await listMaintenanceLog(3);

    expectRequest("/api/v1/fleet/printers/3/maintenance-log");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("records an entry", async () => {
    respondWith({ id: 1 });

    await createMaintenanceLog(3, { category: "nozzle", note: "Replaced 0.4mm" });

    expectRequest("/api/v1/fleet/printers/3/maintenance-log", "POST");
    expect(lastBody()).toMatchObject({ category: "nozzle" });
  });

  it("deletes an entry from its printer", async () => {
    respondWith(null, 204);

    await deleteMaintenanceLog(3, 9);

    expectRequest("/api/v1/fleet/printers/3/maintenance-log/9", "DELETE");
  });
});

describe("maintenance HTTP read cancellation", () => {
  it.each([
    { label: "windows", read: (signal: AbortSignal) => listMaintenanceWindows(3, { signal }) },
    { label: "logs", read: (signal: AbortSignal) => listMaintenanceLog(3, { signal }) },
  ])("passes cancellation to maintenance HTTP reads ($label)", async ({ read }) => {
    const pending = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    const caller = new AbortController();
    const outcome = read(caller.signal).catch((error: Error) => error);
    const signal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal;
    const reason = new DOMException("View left", "AbortError");
    caller.abort(reason);
    expect(signal?.aborted).toBe(true);
    pending.resolve(new Response("[]", { headers: { "content-type": "application/json" } }));
    expect(await outcome).toBe(reason);
  });
});

describe("maintenance transport isolation", () => {
  it.each([
    {
      label: "create window",
      write: () =>
        createMaintenanceWindow(3, {
          starts_at: "2026-08-01T09:00:00Z",
          ends_at: "2026-08-01T11:00:00Z",
        }),
    },
    {
      label: "create log",
      write: () => createMaintenanceLog(3, { category: "service", note: "Done" }),
    },
    { label: "delete window", write: () => deleteMaintenanceWindow(3, 1) },
    { label: "delete log", write: () => deleteMaintenanceLog(3, 1) },
    { label: "routing", write: () => updatePrinterRouting(3, { drain_mode: true }) },
  ])(
    "leaves private caches untouched after maintenance transport writes ($label)",
    async ({ write }) => {
      queryClient.setQueryData(["printers", 99, "maintenance", "log"], [aMaintenanceLog()]);
      respondWith({ ...aMaintenanceWindow() });
      await write();
      expect(queryClient.getQueryState(["printers", 99, "maintenance", "log"])?.isInvalidated).toBe(
        false,
      );
    },
  );

  it("retains genuine maintenance auth failures", async () => {
    storeLogin("", { id: 1, username: "owner", email: null, is_superuser: true });
    fetchMock.mockResolvedValueOnce(
      new Response('{"detail":"session_expired"}', {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(
      createMaintenanceLog(3, { category: "service", note: "Done" }),
    ).rejects.toMatchObject({ status: 401, code: "session_expired" });
    expect(isLoggedIn()).toBe(false);
  });
});
