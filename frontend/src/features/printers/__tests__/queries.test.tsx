/** Printer feature reads preserve remote ownership across acknowledgements and partial failures. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  printerKeys,
  useMaintenanceMutation,
  usePrinterMaintenance,
  usePrinterFileMutation,
} from "../queries";
import { ApiError } from "@/lib/errors";
import { isLoggedIn } from "@/lib/auth-store";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { aMaintenanceLog, aMaintenanceWindow, aPrinter } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

class Socket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send() {}
  close() {}
}
beforeEach(() => setEventSocketFactory(async () => new Socket()));
afterEach(() => vi.unstubAllGlobals());

function Probe({ routing = false }: { routing?: boolean }) {
  const reads = usePrinterMaintenance([1]);
  const mutation = useMaintenanceMutation();
  return (
    <>
      <output>{reads.logs.map((row) => row.note).join(",")}</output>
      <output aria-label="Mutation status">
        {mutation.error instanceof ApiError ? mutation.error.status : mutation.error?.name}
      </output>
      {reads.error && <p>Lookup failed</p>}
      <button onClick={() => void reads.retry()}>Retry</button>
      <button
        onClick={() =>
          void mutation
            .mutateAsync(
              routing
                ? { kind: "routing", printerId: 1, payload: { drain_mode: true } }
                : {
                    kind: "create-log",
                    printerId: 1,
                    payload: { category: "service", note: "Fresh" },
                  },
            )
            .catch(() => {})
        }
      >
        Change
      </button>
    </>
  );
}

describe("printer Query ownership", () => {
  it("cancels obsolete maintenance before confirmed revalidation", async () => {
    const obsolete = Promise.withResolvers<Response>();
    const signals: (AbortSignal | null | undefined)[] = [];
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/fleet/printers/1/maintenance-windows": json([]),
        "GET /api/v1/fleet/printers/1/maintenance-log": (_url, init) => {
          signals.push(init?.signal);
          return signals.length === 1
            ? obsolete.promise
            : json([aMaintenanceLog({ note: "Fresh" })]);
        },
        "POST /api/v1/fleet/printers/1/maintenance-log": json(aMaintenanceLog({ note: "Fresh" })),
      },
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(await screen.findByText("Fresh")).toBeVisible();
    expect(signals[0]?.aborted).toBe(true);
    expect(
      app.requests().filter((request) => request.url.endsWith("/maintenance-windows")),
    ).toHaveLength(1);
    await act(async () => obsolete.resolve(json([aMaintenanceLog({ note: "Obsolete" })])));
    expect(screen.getAllByRole("status")[0]).toHaveTextContent("Fresh");
  });

  it("limits routing reconciliation to routing read models", async () => {
    const app = renderApp(<Probe routing />, {
      seed: [
        [printerKeys.log(99), [aMaintenanceLog({ printer_id: 99 })]],
        [printerKeys.detail(1), aPrinter()],
      ],
      routes: {
        "GET /api/v1/fleet/printers/1/maintenance-windows": json([]),
        "GET /api/v1/fleet/printers/1/maintenance-log": json([]),
        "PATCH /api/v1/fleet/printers/1/routing": json(aPrinter({ drain_mode: true })),
      },
    });
    await waitFor(() => expect(app.client.isFetching()).toBe(0));
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    await waitFor(() =>
      expect(app.client.getQueryState(printerKeys.detail(1))?.isInvalidated).toBe(true),
    );
    expect(app.client.getQueryState(printerKeys.log(99))?.isInvalidated).toBe(false);
    expect(
      app.requests().filter((request) => request.url.endsWith("/maintenance-log")),
    ).toHaveLength(1);
  });

  it("retries only failed maintenance resources", async () => {
    let windows = 0;
    let logs = 0;
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/fleet/printers/1/maintenance-windows": () => {
          windows++;
          return json([aMaintenanceWindow()]);
        },
        "GET /api/v1/fleet/printers/1/maintenance-log": () =>
          ++logs === 1
            ? json({ detail: "offline" }, 503)
            : json([aMaintenanceLog({ note: "Recovered" })]),
      },
    });
    await screen.findByText("Lookup failed");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Recovered")).toBeVisible();
    expect(windows).toBe(1);
    expect(logs).toBe(2);
  });
  it("retains genuine auth failures through the maintenance owner", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/fleet/printers/1/maintenance-windows": json([]),
        "GET /api/v1/fleet/printers/1/maintenance-log": json([]),
        "POST /api/v1/fleet/printers/1/maintenance-log": json({ detail: "session_expired" }, 401),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Mutation status" })).toHaveTextContent("401"),
    );
    expect(isLoggedIn()).toBe(false);
  });
});

function FileProbe({ kind }: { kind: "start" | "sync" | "delete" }) {
  const mutation = usePrinterFileMutation();
  const controller = new AbortController();
  return (
    <>
      <output aria-label="File mutation status">
        {mutation.error instanceof ApiError ? mutation.error.status : mutation.error?.name}
      </output>
      <button
        onClick={() =>
          void mutation
            .mutateAsync(
              kind === "start"
                ? {
                    kind,
                    printerId: 4,
                    signal: controller.signal,
                    payload: { remote_filename: "bracket.gcode", file_id: 20 },
                  }
                : kind === "delete"
                  ? { kind, printerId: 4, signal: controller.signal, fileId: 50 }
                  : { kind, printerId: 4, signal: controller.signal },
            )
            .catch(() => {})
        }
      >
        Change file
      </button>
    </>
  );
}

describe("printer file Query ownership", () => {
  it.each([
    { kind: "start" as const, route: "POST /api/v1/printers/4/start" },
    { kind: "sync" as const, route: "POST /api/v1/printers/4/files/sync" },
    { kind: "delete" as const, route: "DELETE /api/v1/printers/4/files/50" },
  ])(
    "retains genuine auth failures through printer file mutations ($kind)",
    async ({ kind, route }) => {
      renderApp(<FileProbe kind={kind} />, {
        routes: { [route]: json({ detail: "session_expired" }, 401) },
      });
      await userEvent.click(screen.getByRole("button", { name: "Change file" }));
      await waitFor(() =>
        expect(screen.getByRole("status", { name: "File mutation status" })).toHaveTextContent(
          "401",
        ),
      );
      expect(isLoggedIn()).toBe(false);
    },
  );
});
