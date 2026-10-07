/**
 * The printers API client: registering machines, controlling them, and watching them.
 *
 * Two rules run through this module and both are load-bearing. Anything **live** —
 * status, diagnostics, material state, permissions, the machine's own config — is read
 * `fresh`, because these are the values an operator acts on and a cached one is a
 * decision made about a machine that has already moved on. Anything **structural** — the
 * printer list, its files, its job history — may cache.
 *
 * The second rule is that controlling a machine is a POST to a named sub-resource, never
 * a flag on the printer row. Pause, resume, cancel, home, set temperature and emergency
 * stop each have their own endpoint, so each is separately permissioned and separately
 * auditable, and a mis-typed path fails loudly instead of quietly doing something else.
 *
 * The WebSocket is the exception worth stating: a browser cannot set an `Authorization`
 * header on a socket, so the client exchanges a short-lived ticket first and puts it in
 * the query string.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelPrinter,
  createPrinter,
  deletePrinter,
  deletePrinterFile,
  deletePrinterPermission,
  emergencyStopPrinter,
  getDashboard,
  getMoonrakerConfig,
  getPrinter,
  getPrinterDiagnostics,
  getPrinterMaterialState,
  getPrinterStatus,
  homePrinter,
  listPrinterFiles,
  listPrinterJobs,
  listPrinterPermissions,
  listPrinters,
  openPrinterWS,
  pausePrinter,
  resumePrinter,
  sendToPrinter,
  setPrinterTemperature,
  startPrinterFile,
  syncPrinterFiles,
  updatePrinter,
  updatePrinterManualMaterialState,
  updatePrinterPermission,
} from "@/lib/api/printers";
import { queryClient } from "@/lib/query-client";
import { aPrinter, anEditingBase } from "@/test-support/factories";
import { clearLogin } from "@/lib/auth-store";
import { aPrinterPermission } from "@/test-support/permissions";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const PRINTER = { ...anEditingBase(), id: 3, name: "Ender", provider: "moonraker" };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listPrinters", () => {
  it("lists the whole fleet", async () => {
    respondWith([PRINTER]);

    expect(await listPrinters()).toHaveLength(1);
    expectRequest("/api/v1/printers");
  });

  it("filters by group when one is named", async () => {
    respondWith([]);

    await listPrinters("workshop floor");

    // A group is a user-typed label, so it has to survive URL encoding.
    expectRequest("/api/v1/printers?group=workshop%20floor");
  });
});

describe("getDashboard", () => {
  it("reads the dashboard", async () => {
    respondWith({ printers: [] });

    await getDashboard();

    expectRequest("/api/v1/printers/dashboard");
  });
});

describe("getPrinter", () => {
  it("reads one printer", async () => {
    respondWith(PRINTER);

    await getPrinter(3);

    expectRequest("/api/v1/printers/3");
  });
});

describe("createPrinter", () => {
  it("POSTs the new printer", async () => {
    respondWith(PRINTER);

    await createPrinter({ name: "Ender", provider: "moonraker" });

    expectRequest("/api/v1/printers", "POST");
    expect(lastBody()).toMatchObject({ name: "Ender" });
  });
});

describe("updatePrinter", () => {
  it("PATCHes only what changed", async () => {
    respondWith({ ...PRINTER, edit_version: PRINTER.edit_version + 1 });

    await updatePrinter(3, { name: "Ender 3 V2" }, { base: PRINTER });

    expectRequest("/api/v1/printers/3", "PATCH");
    expect(lastBody()).toEqual({ name: "Ender 3 V2" });
  });
  it.each([
    { label: "old version", saved: { ...PRINTER } },
    {
      label: "different history",
      saved: { ...PRINTER, edit_version: 2, edit_epoch: "b".repeat(32) },
    },
    { label: "different printer", saved: { ...PRINTER, id: 99, edit_version: 2 } },
  ])("rejects an acknowledgement with $label", async ({ saved }) => {
    respondWith(saved);
    await expect(updatePrinter(3, { name: "Edit" }, { base: PRINTER })).rejects.toThrow(
      saved.id === 3 ? "Invalid editing acknowledgement" : "printer_identity_mismatch",
    );
  });
});

describe("deletePrinter", () => {
  it("DELETEs the printer", async () => {
    respondWith(null, 204);

    await deletePrinter(3);

    expectRequest("/api/v1/printers/3", "DELETE");
  });
});

describe("live reads", () => {
  it.each([
    ["diagnostics", () => getPrinterDiagnostics(3), "/api/v1/printers/3/diagnostics"],
    ["config", () => getMoonrakerConfig(3), "/api/v1/printers/3/config"],
    ["status", () => getPrinterStatus(3), "/api/v1/printers/3/status"],
    ["material state", () => getPrinterMaterialState(3), "/api/v1/printers/3/material-state"],
    ["permissions", () => listPrinterPermissions(3), "/api/v1/printers/3/permissions"],
  ])("reads %s without touching the cache", async (_name, call, url) => {
    respondWith(_name === "permissions" ? [] : {});

    await call();

    // These are the values an operator acts on; a cached one is a decision made
    // about a machine that has already moved on.
    expectRequest(url);
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("updatePrinterManualMaterialState", () => {
  it("PUTs the operator's correction", async () => {
    respondWith({ tools: [], slots: [] });

    await updatePrinterManualMaterialState(3, { tools: [], slots: [] });

    // PUT, not PATCH: the operator's answer replaces theirs wholesale.
    expectRequest("/api/v1/printers/3/material-state/manual", "PUT");
  });
});

describe("permissions", () => {
  it("PUTs a role for one user on one printer", async () => {
    const permission = aPrinterPermission({ printer_id: 3, user_id: 7, role: "control" });
    respondWith({ ...permission });

    expect(await updatePrinterPermission(3, 7, "control")).toEqual(permission);

    expectRequest("/api/v1/printers/3/permissions/7", "PUT");
    expect(lastBody()).toEqual({ role: "control" });
  });

  it("DELETEs a role", async () => {
    respondWith(null, 204);

    await deletePrinterPermission(3, 7);

    expectRequest("/api/v1/printers/3/permissions/7", "DELETE");
  });
});

describe("sending work to a printer", () => {
  it("sends a library file", async () => {
    respondWith({ id: 1 });

    await sendToPrinter(3, { file_id: 2, start_print: true });

    expectRequest("/api/v1/printers/3/send", "POST");
    expect(lastBody()).toMatchObject({ file_id: 2 });
  });

  it("starts a file already on the machine", async () => {
    respondWith({ id: 1 });

    await startPrinterFile(3, { remote_filename: "part.gcode" });

    // A different endpoint from `send`: nothing is uploaded here.
    expectRequest("/api/v1/printers/3/start", "POST");
  });
});

describe("control", () => {
  it.each([
    ["pause", pausePrinter, "/api/v1/printers/3/pause"],
    ["resume", resumePrinter, "/api/v1/printers/3/resume"],
    ["cancel", cancelPrinter, "/api/v1/printers/3/cancel"],
    ["emergency stop", emergencyStopPrinter, "/api/v1/printers/3/emergency_stop"],
  ])("POSTs %s to its own endpoint", async (_name, call, url) => {
    respondWith(null, 204);

    await call(3);

    // Each act is its own endpoint so each is separately permissioned, and a
    // mis-typed path fails loudly rather than quietly doing something else.
    expectRequest(url, "POST");
  });

  it("sets a heater target", async () => {
    respondWith(null, 204);

    await setPrinterTemperature(3, "bed", 60);

    expectRequest("/api/v1/printers/3/temperature", "POST");
    expect(lastBody()).toEqual({ heater: "bed", target: 60 });
  });

  it("homes the axes it was given", async () => {
    respondWith(null, 204);

    await homePrinter(3, "XY");

    expect(lastBody()).toEqual({ axes: "XY" });
  });

  it("homes everything when no axis is named", async () => {
    respondWith(null, 204);

    await homePrinter(3);

    expect(lastBody()).toEqual({ axes: null });
  });
});

describe("printer files", () => {
  it("lists what is on the machine", async () => {
    respondWith([]);

    await listPrinterFiles(3);

    expectRequest("/api/v1/printers/3/files");
  });

  it("re-reads the machine on a sync", async () => {
    respondWith([]);

    await syncPrinterFiles(3);

    expectRequest("/api/v1/printers/3/files/sync", "POST");
  });

  it("deletes one from the machine", async () => {
    respondWith([]);

    await deletePrinterFile(3, 9);

    expectRequest("/api/v1/printers/3/files/9", "DELETE");
  });
});

describe("listPrinterJobs", () => {
  it("asks for a default page of history", async () => {
    respondWith([]);

    await listPrinterJobs(3);

    expectRequest("/api/v1/printers/3/jobs?limit=50");
  });

  it("asks for the page the caller wants", async () => {
    respondWith([]);

    await listPrinterJobs(3, 5);

    expectRequest("/api/v1/printers/3/jobs?limit=5");
  });
});

describe("openPrinterWS", () => {
  it("exchanges a ticket before opening the socket", async () => {
    respondWith({ ticket: "abc", expires_in: 30 });
    const sockets: string[] = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        constructor(url: string) {
          sockets.push(url);
        }
      },
    );

    await openPrinterWS(3);

    // A browser cannot set an Authorization header on a socket, so the ticket
    // goes in the query string — which is why it is short-lived and single-use.
    expectRequest("/api/v1/printers/3/ws-ticket", "POST");
    expect(sockets[0]).toContain("/api/v1/printers/3/ws?ticket=abc");
  });
});

/** A ticket authorizes only the session and page lifetime that requested it. */
describe("printer ticket lifetimes", () => {
  it("does not open a retired printer ticket", async () => {
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const sockets: string[] = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        constructor(url: string) {
          sockets.push(url);
        }
      },
    );
    const pending = openPrinterWS(3);
    const outcome = pending.catch((error: Error) => error);
    clearLogin();
    headers.resolve(new Response('{"ticket":"old","expires_in":30}'));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(sockets).toEqual([]);
  });

  it("cancels an abandoned printer ticket", async () => {
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const sockets: string[] = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        constructor(url: string) {
          sockets.push(url);
        }
      },
    );
    const controller = new AbortController();
    const pending = openPrinterWS(3, controller.signal);
    const outcome = pending.catch((error: Error) => error);
    controller.abort();
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    headers.resolve(new Response('{"ticket":"old","expires_in":30}'));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(sockets).toEqual([]);
  });
});

describe("printer HTTP read cancellation", () => {
  it.each([
    { label: "detail", read: (signal: AbortSignal) => getPrinter(4, { signal }) },
    { label: "jobs", read: (signal: AbortSignal) => listPrinterJobs(4, 50, { signal }) },
    { label: "files", read: (signal: AbortSignal) => listPrinterFiles(4, { signal }) },
    { label: "diagnostics", read: (signal: AbortSignal) => getPrinterDiagnostics(4, { signal }) },
    { label: "config", read: (signal: AbortSignal) => getMoonrakerConfig(4, { signal }) },
  ])("passes cancellation to printer HTTP reads ($label)", async ({ read }) => {
    const pending = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    const caller = new AbortController();
    const outcome = read(caller.signal).catch((error: Error) => error);
    const signal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal;
    const reason = new DOMException("View left", "AbortError");
    caller.abort(reason);
    expect(signal?.aborted).toBe(true);
    pending.resolve(new Response("{}", { headers: { "content-type": "application/json" } }));
    expect(await outcome).toBe(reason);
  });
});

describe("printer file transport isolation", () => {
  it.each([
    {
      label: "start",
      write: () => startPrinterFile(4, { remote_filename: "bracket.gcode", file_id: 20 }),
    },
    { label: "sync", write: () => syncPrinterFiles(4) },
    { label: "delete", write: () => deletePrinterFile(4, 50) },
  ])("leaves caches untouched after printer file transport writes ($label)", async ({ write }) => {
    queryClient.setQueryData(["printers", 99], aPrinter({ id: 99 }));
    respondWith([]);
    await write();
    expect(queryClient.getQueryState(["printers", 99])?.isInvalidated).toBe(false);
  });
});

describe("permission caller cancellation", () => {
  it("cancels an active permission read", async () => {
    let signal: AbortSignal | null | undefined;
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          signal = init?.signal;
          signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
        }),
    );
    const controller = new AbortController();
    const pending = listPrinterPermissions(3, { signal: controller.signal });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(signal?.aborted).toBe(true);
  });
});
