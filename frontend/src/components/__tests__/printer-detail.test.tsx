/*
 * One printer's console: what it is doing now, and the controls that change it.
 *
 * Everything here is gated twice — by what the *provider* can do and by what the
 * *user* may do — and the two are independent. A Bambu printer cannot be sent
 * raw G-code at all; a viewer may not pause a print on a printer that is
 * perfectly capable of pausing. Rendering a control that fails either check
 * gives the user a button that answers 409 or 403, which reads as the printer
 * being broken rather than as the action being unavailable.
 *
 * The live status arrives over a websocket. A page that cannot open one still
 * has to render the printer it already knows about, because "the socket is
 * down" and "the printer is gone" are different situations and only one of them
 * is worth alarming somebody about.
 *
 * The temperature form is the one place a typo has physical consequences, so it
 * is asserted on the request it produces rather than on the field's value.
 */

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearLogin, storeLogin, retirePrivateSessionScope } from "@/lib/auth-store";
import { PrinterDetailPage } from "@/components/printer-detail";
import {
  aPrinter,
  aPrintJob,
  aPrinterFile,
  aPrinterDiagnostics,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import { queryClient } from "@/lib/query-client";
import { writePrinterCardImagePreference } from "@/lib/printer-card-display";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import type { MoonrakerConfigRead, PrinterRead, PrinterSnapshot } from "@/types";

/**
 * jsdom has no WebSocket, and the live snapshot is the *only* source of the
 * print state the controls key off — so a socket that merely exists is not
 * enough. This one records itself so a test can push the snapshot the printer
 * would have sent.
 */
class FakeSocket {
  static latest: FakeSocket | null = null;
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  readyState = 1;

  constructor() {
    FakeSocket.latest = this;
    FakeSocket.instances.push(this);
  }

  close = vi.fn<() => void>(() => this.onclose?.());
  send() {}
}

/** Push the snapshot the printer would have sent over the live socket. */
async function pushSnapshot(data: PrinterSnapshot) {
  await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
  act(() => {
    FakeSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "snapshot", data }) });
  });
}

/**
 * The body of the last temperature request. Several POSTs go out per gesture, so
 * matching the prefix alone reads whichever one happened to be last.
 */
function temperatureBody(
  requestsWithMethod: (method: string) => { url: string; body: string }[],
): string {
  return (
    requestsWithMethod("POST")
      .filter((call) => call.url.includes("temperature"))
      .at(-1)?.body ?? "{}"
  );
}

/** A printer mid-print, as the live socket reports it. */
const PRINTING = { print_stats: { state: "printing", print_duration: 60, total_duration: 600 } };

const EMPTY_HEATER_HISTORY = { sample_interval_ms: 1000, extruder: [], heater_bed: [] };

const REPORTED_CONFIG = {
  printer_id: 4,
  klipper_config: {
    configfile: {
      config: {
        extruder: { nozzle_diameter: "0.4" },
        "gcode_macro START_PRINT": { gcode: "G28\n  G1 Z10", defaults: { offsets: [0, false] } },
      },
      settings: { extruder: { nozzle_diameter: 0.4 } },
      save_config_pending: false,
    },
  },
  moonraker_config: { config: { server: { port: "7125" } } },
  server_info: { klippy_connected: true },
  printer_info: { hostname: "voron" },
} satisfies MoonrakerConfigRead;

const READY = {
  webhooks: { state: "ready" },
  print_stats: { state: "standby" },
  toolhead: { position: [20, 30, 2], homed_axes: "xyz", extruder: "extruder" },
  extruder: { temperature: 23, can_extrude: false },
  fan: { speed: 0 },
  gcode_move: {
    absolute_coordinates: true,
    absolute_extrude: true,
    speed_factor: 1,
    extrude_factor: 1,
  },
} satisfies PrinterSnapshot;

function renderPrinter(options: RenderAppOptions & { printer?: PrinterRead } = {}) {
  const { printer = aPrinter({ id: 4, name: "Voron" }), routes = {}, ...rest } = options;
  return renderApp(<PrinterDetailPage printerId={4} initialPrinter={printer} />, {
    routes: {
      "GET /api/v1/printers/4": json(printer),
      "GET /api/v1/printers/4/jobs": json([]),
      "GET /api/v1/printers/4/files": json([]),
      "GET /api/v1/printers/4/status": json({ state: "ready" }),
      "GET /api/v1/printers/4/diagnostics": json(aPrinterDiagnostics()),
      "GET /api/v1/printers/4/config": json(REPORTED_CONFIG),
      // The live socket is opened against a short-lived ticket, so the page
      // cannot reach a snapshot at all without this one.
      "POST /api/v1/printers/4/ws-ticket": json({ ticket: "t", expires_in: 60 }),
      "GET /api/v1/printers/4/heater-history": json(EMPTY_HEATER_HISTORY),
      "GET /api/v1/printers/4/bed-mesh": json({ state: "unconfigured" }),
      "GET /api/v1/printers/4/bed-mesh/measurement": json({ supported: false }),
      "GET /api/v1/printers/4/material-state": json({ slots: [], tools: [] }),
      ...routes,
    },
    ...rest,
  });
}

let eventSocket: EventSocket;

beforeEach(() => {
  window.localStorage.clear();
  FakeSocket.latest = null;
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  eventSocket = { onopen: null, onclose: null, onmessage: null, send() {}, close() {} };
  setEventSocketFactory(async () => eventSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrinterDetailPage", () => {
  describe("what it shows", () => {
    it("respects the operator’s local artwork preference", async () => {
      writePrinterCardImagePreference(true);
      renderPrinter({ printer: aPrinter({ id: 4, model_name: "Unknown custom printer" }) });
      const machine = await screen.findByRole("region", { name: "Machine" });
      expect(machine.querySelector("img")).toHaveAttribute(
        "src",
        "/images/printers/generic-fdm.png",
      );

      act(() => writePrinterCardImagePreference(false));
      expect(machine.querySelector("img")).toBeNull();
    });

    it("explains the offline command requirement", async () => {
      renderPrinter({ printer: aPrinter({ id: 4, status: "offline" }) });
      expect(await screen.findByRole("spinbutton", { name: "Hotend target" })).toBeDisabled();
      expect(
        await screen.findByText(
          "Printer is offline. Check its address and connection before sending commands.",
        ),
      ).toBeVisible();
    });

    it("puts the full beta notice in diagnostics", async () => {
      const user = userEvent.setup();
      const note = "Upload and explicit start are beta features.";
      renderPrinter({
        printer: aPrinter({
          id: 4,
          provider: "bambu_lan",
          capabilities: printerCapabilities({
            support_level: "beta",
            support_notes: [note],
            can_send_gcode: false,
          }),
        }),
      });

      expect(await screen.findByText("Beta", { exact: true })).toBeVisible();
      await user.click(screen.getByRole("tab", { name: "Settings" }));
      expect(screen.queryByText(note)).not.toBeInTheDocument();
      await user.click(screen.getByRole("tab", { name: "Diagnostics" }));
      expect(
        within(screen.getByRole("region", { name: "Provider support" })).getByText(note),
      ).toBeVisible();
    });

    it("replaces raw file transport text with recovery guidance", async () => {
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({
          id: 4,
          status: "offline",
          last_error: "transport error: All connection attempts failed",
        }),
      });

      await user.click(await screen.findByRole("tab", { name: "Files" }));

      expect(
        await screen.findByText(
          "Could not connect to the printer. Check its address and network connection, then retry.",
        ),
      ).toBeVisible();
      expect(
        screen.queryByText("transport error: All connection attempts failed"),
      ).not.toBeInTheDocument();
    });

    it("keeps transport detail in Diagnostics", async () => {
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({
          id: 4,
          status: "offline",
          last_error: "transport error: All connection attempts failed",
        }),
      });

      await user.click(await screen.findByRole("tab", { name: "Diagnostics" }));

      expect(
        await screen.findByText("transport error: All connection attempts failed"),
      ).toBeVisible();
    });

    it("names the printer", async () => {
      renderPrinter();

      expect(await screen.findByText("Voron")).toBeInTheDocument();
    });

    it.each(["standby", "complete"])("hides stale job data in state %s", async (state) => {
      renderPrinter();
      await pushSnapshot({
        print_stats: { state, filename: "previous.gcode" },
        virtual_sdcard: { progress: 1 },
      });

      expect(await screen.findByText("No active print")).toBeInTheDocument();
      expect(screen.queryByText("previous.gcode")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    });

    it("does not infer idle before receiving a status", async () => {
      renderPrinter();

      expect(await screen.findByText("Waiting for printer status")).toBeInTheDocument();
    });

    it("gates targeted queueing by print access", async () => {
      renderPrinter({
        printer: aPrinter({
          id: 4,
          access: printerAccess({
            role: "view",
            can_print: false,
            can_control: false,
            can_admin: false,
          }),
        }),
      });
      await screen.findByText("Voron");

      expect(screen.queryByRole("button", { name: "Add to queue" })).not.toBeInTheDocument();
    });

    it("gates targeted queueing by provider capability", async () => {
      renderPrinter({
        printer: aPrinter({ id: 4, capabilities: printerCapabilities({ can_upload: false }) }),
      });
      await screen.findByText("Voron");

      expect(screen.queryByRole("button", { name: "Add to queue" })).not.toBeInTheDocument();
    });

    it("opens on the status tab", async () => {
      renderPrinter();

      expect(await screen.findByRole("tab", { name: "Status" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("stays rendered when the live socket never opens", async () => {
      // "The socket is down" and "the printer is gone" are different situations,
      // and only one of them is worth alarming somebody about.
      renderPrinter();

      expect(await screen.findByText("Voron")).toBeInTheDocument();
    });
  });

  describe("the tabs", () => {
    it("shows the printer's files", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await screen.findByText("Voron");

      await user.click(screen.getByRole("tab", { name: "Files" }));

      expect(await screen.findByText("No printer files synced yet.")).toBeInTheDocument();
    });

    it("shows the print history", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await screen.findByText("Voron");

      await user.click(screen.getByRole("tab", { name: "Jobs" }));

      expect(await screen.findByText("No print jobs yet.")).toBeInTheDocument();
    });

    it("opens the selected job evidence on demand", async () => {
      renderPrinter({
        routes: {
          "GET /api/v1/printers/4/jobs": json([
            aPrintJob({ printer_id: 4, remote_filename: "mount.gcode" }),
          ]),
        },
      });
      await userEvent.click(await screen.findByRole("tab", { name: "Jobs" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await userEvent.click(await screen.findByRole("button", { name: "Evidence" }));
      expect(screen.getByRole("dialog", { name: "mount.gcode" })).toHaveTextContent(
        "Exactly reproducible",
      );
    });

    it("offers the settings tab to an admin", async () => {
      renderPrinter();

      expect(await screen.findByRole("tab", { name: "Settings" })).toBeInTheDocument();
    });

    it("keeps the settings tab from someone who may only view", async () => {
      // The tab is where the credentials live, so hiding it is the boundary
      // rather than a convenience.
      renderPrinter({
        printer: aPrinter({ id: 4, name: "Voron", access: printerAccess({ can_admin: false }) }),
      });

      await screen.findByText("Voron");
      expect(screen.queryByRole("tab", { name: "Settings" })).toBeNull();
    });
  });

  describe("printer workspace sections", () => {
    it("opens the unified workspace from a maintenance bookmark", async () => {
      renderPrinter({
        at: "/?tab=maintenance",
        routes: {
          "GET /api/v1/fleet/printers/4/maintenance-windows": json([]),
          "GET /api/v1/fleet/printers/4/maintenance-log": json([]),
        },
      });
      expect(
        await screen.findByRole("tab", { name: "Materials & maintenance", selected: true }),
      ).toBeVisible();
      expect(
        within(screen.getByRole("region", { name: "Maintenance" })).getByRole("button", {
          name: "Schedule window",
        }),
      ).toBeVisible();
      expect(screen.getByRole("region", { name: "Materials & tools" })).toBeVisible();
    });

    it("keeps maintenance actions out of a print-role material workspace", async () => {
      const app = renderPrinter({
        at: "/?tab=materials",
        printer: aPrinter({
          id: 4,
          access: printerAccess({
            role: "print",
            can_print: true,
            can_control: false,
            can_admin: false,
          }),
        }),
      });
      expect(await screen.findByRole("button", { name: "Add feed" })).toBeEnabled();
      expect(screen.queryByRole("region", { name: "Maintenance" })).not.toBeInTheDocument();
      expect(app.requestsWithMethod("GET").some((call) => call.url.includes("maintenance-"))).toBe(
        false,
      );
    });

    it("omits the empty detail column from healthy diagnostics", async () => {
      renderPrinter({
        at: "/?tab=diagnostics",
        locale: "es",
        routes: {
          "GET /api/v1/printers/4/diagnostics": json(
            aPrinterDiagnostics({
              checks: ["configuration", "provider_info", "live_status"].map((name) => ({
                name,
                ok: true,
                detail: " ",
              })),
            }),
          ),
        },
      });
      const table = within(await screen.findByRole("table"));
      expect(table.getAllByRole("columnheader")).toHaveLength(2);
      expect(table.getByRole("rowheader", { name: "Configuración de la conexión" })).toBeVisible();
      expect(table.getByRole("rowheader", { name: "Información de la impresora" })).toBeVisible();
      expect(table.getByRole("rowheader", { name: "Estado en directo" })).toBeVisible();
    });

    it("keeps the detail column for a code-only check", async () => {
      renderPrinter({
        at: "/?tab=diagnostics",
        routes: {
          "GET /api/v1/printers/4/diagnostics": json(
            aPrinterDiagnostics({
              ok: false,
              checks: [
                { name: "custom_provider_check", ok: false, code: "provider_transport_error" },
              ],
            }),
          ),
        },
      });
      const table = within(await screen.findByRole("table", { name: "Connection check results" }));
      expect(table.getByRole("columnheader", { name: "Detail" })).toBeVisible();
      expect(table.getByRole("rowheader", { name: "custom_provider_check" })).toBeVisible();
      expect(table.getByText("provider_transport_error")).toBeVisible();
    });

    it("shows the failed diagnostic check with its service detail", async () => {
      renderPrinter({
        at: "/?tab=diagnostics",
        routes: {
          "GET /api/v1/printers/4/diagnostics": json(
            aPrinterDiagnostics({
              ok: false,
              checks: [
                {
                  name: "Provider connection",
                  ok: false,
                  detail: "Connection timed out",
                  code: "provider_transport_error",
                },
              ],
            }),
          ),
        },
      });
      const table = await screen.findByRole("table", { name: "Connection check results" });
      const failed = within(table).getByRole("row", { name: /Provider connection/ });
      expect(failed).toHaveTextContent("Failed");
      expect(failed).toHaveTextContent("Connection timed out");
      expect(failed).toHaveTextContent("provider_transport_error");
    });

    it("retries diagnostics after a failed request", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        at: "/?tab=diagnostics",
        routes: {
          "GET /api/v1/printers/4/diagnostics": json({ detail: "provider_transport_error" }, 503),
        },
      });
      const diagnostics = within(await screen.findByRole("region", { name: "Connection checks" }));
      const retry = await diagnostics.findByRole("button", { name: "Retry" });
      app.route({ "GET /api/v1/printers/4/diagnostics": json(aPrinterDiagnostics()) });
      await user.click(retry);
      expect(await diagnostics.findByText("Provider reachable")).toBeVisible();
      expect(diagnostics.getByText(/Last checked/)).toBeVisible();
    });
  });

  describe("what the user may do", () => {
    it("offers pause to someone who may control the printer", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      expect(await screen.findByRole("button", { name: /Pause/ })).toBeEnabled();
    });

    it("withholds pause from someone who may only view", async () => {
      // The control stays visible and says why. A missing button reads as a
      // broken page; a disabled one reads as "not yours".
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          access: printerAccess({ can_print: false, can_control: false, can_admin: false }),
        }),
      });
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      expect(await screen.findByRole("button", { name: "Pause" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    });

    it("withholds a control the provider cannot perform", async () => {
      // A button that answers 409 reads as the printer being broken rather than
      // as the action being unsupported.
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          capabilities: printerCapabilities({ can_pause: false }),
        }),
      });
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    });

    it("withholds pause from a printer that is not printing", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot({ print_stats: { state: "paused" } });

      expect(screen.queryByRole("button", { name: /Pause/ })).not.toBeInTheDocument();
    });

    it("offers resume to a paused printer", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot({ print_stats: { state: "paused" } });

      expect(await screen.findByRole("button", { name: /Resume/ })).toBeEnabled();
    });
  });

  describe("controlling a print", () => {
    it("asks the printer to pause", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/pause": json({ ok: true }) },
      });
      await screen.findByText("Voron");
      await pushSnapshot(PRINTING);

      await user.click(await screen.findByRole("button", { name: /Pause/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/pause"))).toBe(true),
      );
    });

    it("reports a rejected pause beside the measured print state", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      renderPrinter({ routes: { "POST /api/v1/printers/4/pause": () => pending.promise } });
      await pushSnapshot({ print_stats: { state: "printing", filename: "bracket.gcode" } });
      const job = within(screen.getByRole("region", { name: "Current job" }));

      await user.click(job.getByRole("button", { name: "Pause" }));
      expect(job.getByText("Sending command…")).toBeVisible();
      expect(job.getByText("bracket.gcode")).toBeVisible();
      expect(job.getByRole("button", { name: "Cancel" })).toBeDisabled();

      await act(async () => pending.resolve(json({ detail: "printer_not_ready" }, 409)));
      expect(job.getByRole("alert")).toHaveTextContent("The printer is not ready.");
      expect(job.getByText("printing")).toBeVisible();
      expect(job.getByRole("button", { name: "Pause" })).toBeEnabled();
    });

    it("asks the printer to cancel", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/cancel": json({ ok: true }) },
      });
      await screen.findByText("Voron");
      await pushSnapshot(PRINTING);

      await user.click(await screen.findByRole("button", { name: /Cancel/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/cancel"))).toBe(true),
      );
    });

    it("shows how far along the print is", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot({ ...PRINTING, virtual_sdcard: { progress: 0.1 } });
      expect(await screen.findByText("10%")).toBeVisible();
    });
  });

  describe("setting a temperature", () => {
    it("sends the hotend target the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      await pushSnapshot(READY);
      const inputs = screen.getAllByPlaceholderText("°C");
      await user.type(inputs[0], "215");
      await user.click(
        screen.getByRole("button", {
          name: "Set Hotend target",
        }),
      );

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("temperature"))?.body ??
              "{}",
          ),
        ).toMatchObject({ target: 215 }),
      );
    });

    it("sends the bed target separately from the hotend", async () => {
      // One request per heater; sending both under one name is how a bed ends
      // up at an extruder temperature.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      await pushSnapshot(READY);
      await user.type(screen.getAllByPlaceholderText("°C")[1], "60");
      await user.click(
        screen.getByRole("button", {
          name: "Set Bed target",
        }),
      );

      await waitFor(() =>
        expect(JSON.parse(temperatureBody(requestsWithMethod))).toMatchObject({
          heater: "bed",
          target: 60,
        }),
      );
    });

    it.each(["printing", "paused"])("sets an individual target during a %s job", async (state) => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await pushSnapshot({ ...READY, print_stats: { state } });

      const thermal = within(screen.getByRole("region", { name: "Temperatures" }));
      await user.type(thermal.getByRole("spinbutton", { name: "Bed target" }), "55{Enter}");

      expect(await thermal.findByText("Target command sent for 55 °C")).toBeVisible();
      expect(JSON.parse(temperatureBody(app.requestsWithMethod))).toEqual({
        heater: "bed",
        target: 55,
      });
    });

    it("keeps paired heater actions unavailable during a paused print", async () => {
      renderPrinter();
      await pushSnapshot({ ...READY, print_stats: { state: "paused" } });
      const thermal = within(screen.getByRole("region", { name: "Temperatures" }));

      expect(thermal.getByRole("button", { name: "Preheat" })).toBeDisabled();
      expect(thermal.getByRole("button", { name: "Cooldown" })).toBeDisabled();
    });

    it("announces a pending target without allowing another write", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": () => pending.promise },
      });
      await pushSnapshot({ ...READY, ...PRINTING });
      const thermal = within(screen.getByRole("region", { name: "Temperatures" }));
      await user.type(thermal.getByRole("spinbutton", { name: "Hotend target" }), "210{Enter}");

      expect(thermal.getByText("Sending command…")).toBeVisible();
      expect(thermal.getByRole("spinbutton", { name: "Bed target" })).toHaveAttribute("readonly");
      expect(thermal.getByRole("button", { name: "Set Hotend target" })).toBeDisabled();
      expect(
        app.requestsWithMethod("POST").filter((call) => call.url.endsWith("/temperature")),
      ).toHaveLength(1);
      await act(async () => pending.resolve(json({ ok: true })));
      expect(thermal.getByText("Target command sent for 210 °C")).toBeVisible();
    });

    it("keeps a keyboard draft through a newer measured target", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await pushSnapshot({ ...READY, heater_bed: { temperature: 32, target: 60 } });
      const target = screen.getByRole("spinbutton", { name: "Bed target" });
      await user.type(target, "65");
      await pushSnapshot({ heater_bed: { temperature: 33, target: 62 } });
      expect(target).toHaveFocus();
      expect(target).toHaveValue(65);
      expect(screen.getByText("Target 62 °C")).toBeVisible();
    });

    it("keeps a rejected target editable with accessible feedback", async () => {
      const user = userEvent.setup();
      renderPrinter({
        routes: {
          "POST /api/v1/printers/4/temperature": json({ detail: "printer_not_ready" }, 409),
        },
      });
      await pushSnapshot({ ...READY, print_stats: { state: "paused" } });
      const thermal = within(screen.getByRole("region", { name: "Temperatures" }));
      const target = thermal.getByRole("spinbutton", { name: "Hotend target" });
      await user.type(target, "210{Enter}");

      expect(await thermal.findByRole("alert")).toHaveTextContent("The printer is not ready.");
      expect(target).toHaveValue(210);
      expect(target).toBeEnabled();
    });
  });

  describe("preheating", () => {
    it("sets both heaters for the chosen material", async () => {
      // A preset is one gesture standing for two heaters; setting only the
      // hotend leaves the bed cold and the first layer will not stick.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      await pushSnapshot(READY);
      await user.click(screen.getByRole("radio", { name: "PLA 200 / 60 °C" }));
      await user.keyboard("{ArrowRight}");
      expect(screen.getByRole("radio", { name: "PETG 240 / 80 °C" })).toBeChecked();
      expect(screen.getByRole("radio", { name: "PLA 200 / 60 °C" })).not.toBeChecked();
      expect(
        requestsWithMethod("POST").filter((call) => call.url.endsWith("/temperature")),
      ).toEqual([]);
      await user.click(screen.getByRole("button", { name: "Preheat" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST")
            .filter((call) => call.url.includes("temperature"))
            .map((call) => JSON.parse(call.body)),
        ).toEqual([
          { heater: "extruder", target: 240 },
          { heater: "bed", target: 80 },
        ]),
      );
    });

    it("cools both heaters down together", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      await pushSnapshot(READY);
      await user.click(screen.getByRole("button", { name: "Cooldown" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST")
            .filter((call) => call.url.includes("temperature"))
            .map((call) => JSON.parse(call.body)),
        ).toEqual([
          { heater: "extruder", target: 0 },
          { heater: "bed", target: 0 },
        ]),
      );
    });

    it("localizes the cooldown confirmation", async () => {
      const user = userEvent.setup();
      renderPrinter({
        locale: "es",
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      await pushSnapshot(READY);
      await user.click(screen.getByRole("button", { name: "Enfriar" }));

      expect(await screen.findByText("Comando de enfriamiento enviado")).toBeInTheDocument();
    });

    it("offers no presets on a printer that takes no G-code", async () => {
      // Bambu in LAN mode accepts jobs but not raw commands, so a preheat
      // button there is a button that answers 409.
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          capabilities: printerCapabilities({ can_send_gcode: false }),
        }),
      });

      await screen.findByText("Voron");
      expect(screen.queryByRole("spinbutton", { name: "Hotend target" })).not.toBeInTheDocument();
    });

    it("keeps preheating from someone who may not control the printer", async () => {
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          access: printerAccess({ can_control: false }),
        }),
      });

      await screen.findByText("Voron");
      expect(screen.queryByRole("spinbutton", { name: "Hotend target" })).not.toBeInTheDocument();
    });
  });

  it("distinguishes coincident heater curves when both series are selected", async () => {
    const user = userEvent.setup();
    const samples = [
      { age_ms: 4000, temperature: 22, target: 0 },
      { age_ms: 5000, temperature: 22, target: 0 },
    ];
    renderPrinter({
      routes: {
        "GET /api/v1/printers/4/heater-history": json({
          sample_interval_ms: 1000,
          extruder: samples,
          heater_bed: samples,
        }),
      },
    });

    const chart = await screen.findByRole("figure", { name: "Heater history" });
    await waitFor(() => {
      const hotend = within(chart).getByRole("group", { name: "Hotend" });
      const bed = within(chart).getByRole("group", { name: "Bed" });
      const hotendLine = hotend.querySelector("polyline");
      const bedLine = bed.querySelector("polyline");
      expect(hotendLine).toBeInTheDocument();
      expect(bedLine).toBeInTheDocument();
      expect(hotendLine).not.toHaveAttribute("stroke-dasharray");
      expect(bedLine).not.toHaveAttribute("stroke-dasharray");
      expect(hotendLine).toHaveAttribute("points", bedLine?.getAttribute("points"));
    });

    const bed = screen.getByRole("button", { name: "Plot Bed" });
    await user.click(bed);
    expect(bed).toHaveAttribute("aria-pressed", "false");
    expect(chart.querySelectorAll("polyline")).toHaveLength(1);
    expect(chart).toHaveTextContent("Hotend observed range");
    expect(chart).not.toHaveTextContent("Bed observed range");
  });

  it("labels recorded temperatures separately from observed live telemetry", async () => {
    renderPrinter({
      routes: {
        "GET /api/v1/printers/4/heater-history": json({
          sample_interval_ms: 1000,
          extruder: [{ age_ms: 3000, temperature: 123, target: 200 }],
          heater_bed: [],
        }),
      },
    });

    await pushSnapshot(READY);

    const history = screen.getByRole("figure", { name: "Heater history" });
    expect(
      await within(history).findByText(
        "Moonraker history and live readings · historical times approximate",
      ),
    ).toBeVisible();
    expect(history).toHaveTextContent("23 °C to 123 °C");
  });

  it("presents a single recorded reading as a number before there is a line", async () => {
    renderPrinter({
      routes: {
        "GET /api/v1/printers/4/heater-history": json({
          sample_interval_ms: 1000,
          extruder: [{ age_ms: 3000, temperature: 123, target: 200 }],
          heater_bed: [],
        }),
      },
    });

    const history = await screen.findByRole("figure", { name: "Heater history" });
    expect(await within(history).findByText("Last observed 123 °C")).toBeVisible();
    expect(history.querySelector("circle, polyline")).not.toBeInTheDocument();
  });

  describe("reported configuration", () => {
    const fileRoutes = { "GET /api/v1/printers/4/config/files": json([]) };

    it("does not fetch configuration for a Moonraker viewer", async () => {
      const { requestsWithMethod } = renderPrinter({
        at: "/?tab=config",
        printer: aPrinter({ id: 4, access: printerAccess({ can_admin: false }) }),
      });
      expect(await screen.findByRole("figure", { name: "Heater history" })).toBeVisible();
      expect(screen.queryByRole("tab", { name: "Config" })).not.toBeInTheDocument();
      expect(requestsWithMethod("GET").some((call) => call.url.includes("/4/config"))).toBe(false);
    });

    it("searches real Klipper sections inside the configfile wrapper", async () => {
      const user = userEvent.setup();
      renderPrinter({ at: "/?tab=config", routes: fileRoutes });

      await user.click(await screen.findByRole("tab", { name: "Reported configuration" }));
      const reported = screen.getByRole("tabpanel", { name: "Reported configuration" });
      const search = await within(reported).findByRole("searchbox");
      await user.type(search, "does not exist");
      expect(within(reported).getByText("No matching sections.")).toBeVisible();
      await user.clear(search);
      await user.type(search, "start_print");
      const macro = within(reported).getByRole("button", { name: "gcode_macro START_PRINT" });
      await user.click(macro);
      expect(macro).toHaveAttribute("aria-current", "true");
      const values = within(reported).getByRole("region", { name: "gcode_macro START_PRINT" });
      expect(within(values).getByText("gcode", { exact: true })).toBeVisible();
      expect(within(values).getByText(/G28/).textContent).toBe("G28\n  G1 Z10");
      expect(within(values).getByText("offsets", { exact: true })).toBeVisible();
      expect(within(values).getByText("0", { exact: true })).toBeVisible();
      expect(within(values).getByText("false", { exact: true })).toBeVisible();
    });

    it("copies the complete source payload when the selected source changes", async () => {
      const user = userEvent.setup();
      renderPrinter({ at: "/?tab=config", routes: fileRoutes });
      await user.click(await screen.findByRole("tab", { name: "Reported configuration" }));
      await user.click(screen.getByRole("tab", { name: "Moonraker config" }));
      expect(await screen.findByRole("region", { name: "server" })).toHaveTextContent("7125");
      await user.click(screen.getByRole("button", { name: "Copy JSON" }));
      expect(JSON.parse(await navigator.clipboard.readText())).toEqual(
        REPORTED_CONFIG.moonraker_config,
      );
      await user.click(screen.getByRole("tab", { name: "Printer info" }));
      expect(screen.getByRole("region", { name: "Reported values" })).toHaveTextContent("voron");
    });

    it("retries a failed provider request from the reported configuration view", async () => {
      const user = userEvent.setup();
      const gate = Promise.withResolvers<Response>();
      const read = vi
        .fn<() => Response | Promise<Response>>()
        .mockReturnValueOnce(gate.promise)
        .mockImplementation(() => json(REPORTED_CONFIG));
      renderPrinter({
        at: "/?tab=config",
        routes: { ...fileRoutes, "GET /api/v1/printers/4/config": read },
      });
      await user.click(await screen.findByRole("tab", { name: "Reported configuration" }));
      const reported = screen.getByRole("tabpanel", { name: "Reported configuration" });
      expect(within(reported).getByRole("status")).toHaveTextContent("Loading config…");
      expect(within(reported).getByRole("button", { name: "Copy JSON" })).toBeDisabled();
      gate.resolve(json({ detail: "printer_unreachable" }, 502));
      expect(await within(reported).findByRole("alert")).toBeVisible();
      await user.click(within(reported).getByRole("button", { name: "Retry" }));
      expect(await within(reported).findByRole("region", { name: "extruder" })).toBeVisible();
    });
  });

  describe("operator controls", () => {
    it("jogs a homed axis by the selected signed distance", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "POST /api/v1/printers/4/jog": json({ ok: true }) },
      });
      await pushSnapshot(READY);

      await user.click(screen.getByRole("button", { name: "0.1 mm" }));
      await user.click(screen.getByRole("button", { name: "Y−" }));

      await waitFor(() =>
        expect(
          JSON.parse(
            app.requestsWithMethod("POST").find((call) => call.url.endsWith("/jog"))?.body ?? "{}",
          ),
        ).toEqual({ axis: "y", distance: -0.1 }),
      );
    });

    it("offers bed inspection to a printer viewer", async () => {
      renderPrinter({
        printer: aPrinter({
          id: 4,
          access: printerAccess({
            role: "view",
            can_control: false,
            can_print: false,
            can_admin: false,
          }),
        }),
      });

      expect(await screen.findByText("Klipper bed mesh is not configured.")).toBeVisible();
    });

    it("clears the homing instruction after all axes are reported homed", async () => {
      renderPrinter();
      await pushSnapshot({ ...READY, toolhead: { homed_axes: "x" } });
      const toolhead = within(screen.getByRole("region", { name: "Toolhead" }));
      expect(toolhead.getByText("Home an axis before moving it.")).toBeVisible();
      await pushSnapshot(READY);
      expect(toolhead.queryByText("Home an axis before moving it.")).not.toBeInTheDocument();
    });

    it("keeps unhomed axes disabled", async () => {
      renderPrinter();
      await pushSnapshot({ ...READY, toolhead: { homed_axes: "x" } });

      expect(screen.getByRole("button", { name: "X+" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Z+" })).toBeDisabled();
    });

    it.each([
      ["Home Z", "z"],
      ["Home all", null],
    ])("sends a %s command", async (label, axes) => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "POST /api/v1/printers/4/home": json({ ok: true }) },
      });
      await pushSnapshot(READY);

      await user.click(screen.getByRole("button", { name: label }));

      await waitFor(() =>
        expect(
          JSON.parse(
            app.requestsWithMethod("POST").find((call) => call.url.endsWith("/home"))?.body ?? "{}",
          ),
        ).toEqual({ axes }),
      );
    });

    it.each([
      ["Feed", 5],
      ["Retract", -5],
    ])("sends signed filament movement for %s", async (label, distance) => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "POST /api/v1/printers/4/extrude": json({ ok: true }) },
      });
      await pushSnapshot({ ...READY, extruder: { temperature: 205, can_extrude: true } });

      await user.click(screen.getByRole("button", { name: `${label} 5 mm` }));

      await waitFor(() =>
        expect(
          JSON.parse(
            app.requestsWithMethod("POST").find((call) => call.url.endsWith("/extrude"))?.body ??
              "{}",
          ),
        ).toEqual({ distance }),
      );
    });

    it("withholds filament movement while the hotend is cold", async () => {
      renderPrinter();
      await pushSnapshot(READY);

      expect(screen.getByRole("button", { name: "Feed 5 mm" })).toBeDisabled();
    });

    it.each([
      ["Part cooling fan", "fan", 0],
      ["Print speed", "speed", 125],
      ["Extrusion flow", "flow", 90],
    ])("tunes %s during a paused print", async (label, control, percent) => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "POST /api/v1/printers/4/tune": json({ ok: true }) },
      });
      await pushSnapshot({ ...READY, print_stats: { state: "paused" } });

      await user.type(
        screen.getByRole("spinbutton", { name: `${label} target (%)` }),
        String(percent),
      );
      await user.click(screen.getByRole("button", { name: `Set ${label}` }));

      await waitFor(() =>
        expect(
          JSON.parse(
            app.requestsWithMethod("POST").find((call) => call.url.endsWith("/tune"))?.body ?? "{}",
          ),
        ).toEqual({ control, percent }),
      );
    });

    it("rejects target speeds outside the machine-control range", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await pushSnapshot(READY);

      await user.type(screen.getByRole("spinbutton", { name: "Print speed target (%)" }), "151");

      expect(screen.getByRole("button", { name: "Set Print speed" })).toBeDisabled();
    });

    it("keeps preheat disabled during an active print", async () => {
      renderPrinter();
      await pushSnapshot({ ...READY, ...PRINTING });

      expect(screen.getByRole("button", { name: "Preheat" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "X+" })).toBeDisabled();
    });

    it("turns a structured travel rejection into operator feedback", async () => {
      const user = userEvent.setup();
      renderPrinter({
        routes: {
          "POST /api/v1/printers/4/jog": json({ detail: "move_out_of_bounds" }, 409),
        },
      });
      await pushSnapshot(READY);

      await user.click(screen.getByRole("button", { name: "X+" }));

      const toolhead = within(screen.getByRole("region", { name: "Toolhead" }));
      expect(await toolhead.findByRole("alert")).toHaveTextContent(
        "The move would go outside the printer's travel limits.",
      );
    });
  });

  describe("the emergency stop", () => {
    it("asks before halting the printer", async () => {
      // It requires a firmware restart afterwards, so a misclick costs a print
      // and a trip to the machine.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot(READY);
      await user.click(screen.getByRole("button", { name: /Emergency stop|E-Stop/i }));

      expect(requestsWithMethod("POST").some((call) => call.url.includes("emergency_stop"))).toBe(
        false,
      );
    });

    it("sends one confirmed emergency stop while bed measurement is pending", async () => {
      const user = userEvent.setup();
      const measurement = Promise.withResolvers<Response>();
      const stop = Promise.withResolvers<Response>();
      const endpoint = "/api/v1/printers/4/bed-mesh/measurement";
      const app = renderPrinter({
        routes: {
          "GET /api/v1/printers/4/bed-mesh": json({ state: "inactive" }),
          [`GET ${endpoint}`]: json({ supported: true }),
          [`POST ${endpoint}`]: () => measurement.promise,
          "POST /api/v1/printers/4/emergency_stop": () => stop.promise,
        },
      });
      const stopped = () =>
        app.requestsWithMethod("POST").filter((call) => call.url.endsWith("/emergency_stop"));
      await pushSnapshot(READY);

      await user.click(await screen.findByRole("button", { name: "Measure bed" }));
      const measureDialog = screen.getByRole("dialog", { name: "Measure bed" });
      await user.click(within(measureDialog).getByRole("button", { name: "Measure bed" }));
      expect(await screen.findByText("Measuring bed…")).toBeVisible();
      await waitFor(() => expect(measureDialog).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: "X+" })).toBeDisabled();

      await user.click(screen.getByRole("button", { name: "Emergency stop" }));
      const dialog = screen.getByRole("dialog", { name: "Emergency stop printer?" });
      expect(stopped()).toHaveLength(0);
      const confirm = within(dialog).getByRole("button", { name: "Emergency stop" });
      await user.click(confirm);
      await waitFor(() => expect(stopped()).toHaveLength(1));
      expect(confirm).toBeDisabled();
      await user.click(confirm);
      expect(stopped()).toHaveLength(1);

      await act(async () => stop.resolve(json({ ok: true })));
      await waitFor(() => expect(dialog).not.toBeInTheDocument());
      expect(screen.getByText("Measuring bed…")).toBeVisible();
      await act(async () => measurement.resolve(json({ detail: "bed_measurement_unknown" }, 502)));
    });

    it("halts the printer once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/emergency_stop": json(null, 204) },
      });
      await screen.findByText("Voron");
      await pushSnapshot(READY);
      await user.click(screen.getByRole("button", { name: /Emergency stop|E-Stop/i }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", {
          name: "Emergency stop",
        }),
      );

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("emergency_stop"))).toBe(
          true,
        ),
      );
    });
  });

  describe("the files on the printer", () => {
    /** Open the Files tab, which is where every file action lives. */
    async function openFiles(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Voron");
      await user.click(screen.getByRole("tab", { name: "Files" }));
      await screen.findByRole("button", { name: "Sync printer files" });
    }

    it("lists what is on the machine", async () => {
      const user = userEvent.setup();
      renderPrinter({ routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) } });

      await openFiles(user);

      expect(
        await within(await screen.findByRole("table", { name: "Printer files" })).findByText(
          "bracket.gcode",
        ),
      ).toBeInTheDocument();
    });

    it("re-reads the machine when asked", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/files/sync": json([aPrinterFile()]) },
      });
      await openFiles(user);

      await user.click(screen.getByRole("button", { name: /Sync/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/files/sync"))).toBe(
          true,
        ),
      );
    });

    it("starts the file the user picked", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: {
          "GET /api/v1/printers/4/files": json([aPrinterFile()]),
          "POST /api/v1/printers/4/start": json({ id: 30 }),
        },
      });
      await openFiles(user);

      await user.click(await screen.findByRole("button", { name: /Start/ }));

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.endsWith("/start"))?.body ?? "{}",
          ),
        ).toMatchObject({ remote_filename: "bracket.gcode" }),
      );
    });

    it("will not let a viewer start anything", async () => {
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          access: printerAccess({ can_print: false }),
        }),
        routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) },
      });
      await openFiles(user);

      expect(await screen.findByRole("button", { name: /Start/ })).toBeDisabled();
    });

    it("refuses to delete the file currently printing", async () => {
      // Deleting it mid-print is how a running job loses the file underneath
      // it, and the printer stops somewhere in the middle.
      const user = userEvent.setup();
      renderPrinter({ routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) } });
      await openFiles(user);
      await pushSnapshot({ print_stats: { state: "printing", filename: "bracket.gcode" } });

      await waitFor(() => expect(screen.getByRole("button", { name: /Delete/ })).toBeDisabled());
    });

    it("asks before deleting a file off the machine", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) },
      });
      await openFiles(user);

      await user.click(await screen.findByRole("button", { name: /Delete/ }));

      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("deletes the file once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: {
          "GET /api/v1/printers/4/files": json([aPrinterFile()]),
          "DELETE /api/v1/printers/4/files/50": json([]),
        },
      });
      await openFiles(user);
      await user.click(await screen.findByRole("button", { name: /Delete/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: /Delete/ }),
      );

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.endsWith("/files/50"))).toBe(
          true,
        ),
      );
    });

    it("offers no start on a printer that cannot begin a job", async () => {
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          capabilities: printerCapabilities({ can_start: false }),
        }),
        routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) },
      });
      await openFiles(user);

      expect(await screen.findByRole("button", { name: /Start/ })).toBeDisabled();
    });

    it("offers no deletion on a provider that cannot do it", async () => {
      // Only Moonraker exposes a delete; elsewhere the button would 501.
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({ id: 4, name: "Voron", provider: "octoprint" }),
        routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) },
      });
      await openFiles(user);

      expect(await screen.findByRole("button", { name: /Delete/ })).toBeDisabled();
    });
  });

  describe("the printer's settings", () => {
    /** Open the Settings tab, which only an admin is shown. */
    async function openSettings(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Voron");
      await user.click(screen.getByRole("tab", { name: "Settings" }));
      await screen.findByLabelText("Name");
    }

    it("uses the local 3D printer artwork in Settings before model artwork is enabled", async () => {
      renderPrinter({
        at: "/?tab=settings",
        printer: aPrinter({ id: 4, provider: "bambu_lan", model_name: "Bambu Lab P1S" }),
      });
      const machine = await screen.findByRole("region", { name: "Machine" });
      expect(machine.querySelector("img")).toHaveAttribute(
        "src",
        "/images/printers/generic-fdm.png",
      );
    });

    it("hides the Settings portrait when artwork is turned off", async () => {
      renderPrinter({ at: "/?tab=settings" });
      const machine = await screen.findByRole("region", { name: "Machine" });
      act(() => writePrinterCardImagePreference(false));
      expect(machine.querySelector("img")).not.toBeInTheDocument();
    });

    it("falls back to local artwork when the opt-in Settings portrait fails", async () => {
      writePrinterCardImagePreference(true);
      renderPrinter({
        at: "/?tab=settings",
        printer: aPrinter({ id: 4, provider: "bambu_lan", model_name: "Bambu Lab P1S" }),
      });
      const machine = await screen.findByRole("region", { name: "Machine" });
      const image = machine.querySelector("img");
      expect(image).toHaveAttribute("src", expect.stringContaining("Bambu%20Lab%20P1S_cover.png"));
      if (!image) throw new Error("No Settings portrait");
      fireEvent.error(image);
      expect(image).toHaveAttribute("src", "/images/printers/generic-fdm.png");
    });

    it("saves the details the operator changed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json(aPrinter({ id: 4, name: "Voron 2.4" })) },
      });
      await openSettings(user);
      const name = screen.getByLabelText("Name");
      await user.clear(name);
      await user.type(name, "Voron 2.4");

      await user.click(screen.getAllByRole("button", { name: /Save changes/ })[0]);

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          name: "Voron 2.4",
        }),
      );
    });

    it("leaves the stored secret alone when the field is untouched", async () => {
      // The credential is never read back, so sending an empty string would
      // replace a working access code with nothing and take the printer
      // offline.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json(aPrinter({ id: 4, name: "Voron" })) },
      });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");

      await user.click(screen.getAllByRole("button", { name: /Save changes/ })[0]);

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).not.toHaveProperty(
          "api_key",
        ),
      );
    });

    it("sends a new secret when one is typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json(aPrinter({ id: 4, name: "Voron" })) },
      });
      await openSettings(user);
      await user.type(screen.getByPlaceholderText("Unchanged"), "not-a-real-key");

      await user.click(screen.getAllByRole("button", { name: /Save changes/ })[0]);

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          api_key: "not-a-real-key",
        }),
      );
    });

    it("preserves a conflicted printer draft for an explicit revised save", async () => {
      const user = userEvent.setup();
      const base = {
        ...aPrinter({ id: 4, name: "Voron" }),
        edit_epoch: "a".repeat(32),
        edit_version: 1,
      };
      const latest = {
        ...base,
        name: "Other operator",
        notes: "Keep these notes",
        edit_version: 2,
      };
      const app = renderPrinter({
        printer: base,
        routes: {
          "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412),
        },
      });
      await openSettings(user);
      await user.clear(screen.getByLabelText("Name"));
      await user.type(screen.getByLabelText("Name"), "My draft");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      const review = await screen.findByRole("button", { name: "Review current settings" });
      expect(screen.getByLabelText("Name")).toHaveValue("My draft");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      app.route({ "GET /api/v1/printers/4": json(latest) });
      await user.click(review);
      expect(
        await within(screen.getByRole("status", { name: "Current printer settings" })).findByText(
          "Other operator",
        ),
      ).toBeVisible();
      expect(screen.getByLabelText("Name")).toHaveValue("My draft");
      app.route({
        "PATCH /api/v1/printers/4": json({ ...latest, name: "My draft", edit_version: 3 }),
      });
      await user.click(screen.getByRole("button", { name: "Save revised changes" }));
      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(2));
      expect(JSON.parse(app.requestsWithMethod("PATCH")[1].body)).toEqual({ name: "My draft" });
      expect(await screen.findByRole("heading", { name: "My draft" })).toBeVisible();
    });

    it("requires review after an uncertain printer save", async () => {
      const user = userEvent.setup();
      const base = {
        ...aPrinter({ id: 4, name: "Voron" }),
        edit_epoch: "a".repeat(32),
        edit_version: 1,
      };
      const app = renderPrinter({
        printer: base,
        routes: {
          "PATCH /api/v1/printers/4": () => Promise.reject(new TypeError("Connection lost")),
        },
      });
      await openSettings(user);
      await user.clear(screen.getByLabelText("Name"));
      await user.type(screen.getByLabelText("Name"), "Possibly saved");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      expect(await screen.findByRole("button", { name: "Review current settings" })).toBeVisible();
      expect(screen.getAllByRole("button", { name: "Save changes" })[0]).toBeDisabled();
      expect(screen.getByLabelText("Name")).toHaveValue("Possibly saved");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      app.route({
        "GET /api/v1/printers/4": json(
          aPrinter({ id: 4, name: "Possibly saved", edit_version: 2 }),
        ),
      });
      await user.click(screen.getByRole("button", { name: "Review current settings" }));
      await user.click(await screen.findByRole("button", { name: "Use current values" }));
      expect(screen.getByLabelText("Name")).toHaveValue("Possibly saved");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("sends the original settings base after a background refetch", async () => {
      const user = userEvent.setup();
      const base = aPrinter({ id: 4, name: "Voron" });
      const sent: Headers[] = [];
      const app = renderPrinter({
        printer: base,
        routes: {
          "PATCH /api/v1/printers/4": (_url, init) => {
            sent.push(new Headers(init?.headers));
            return json({ detail: "edit_conflict" }, 412);
          },
        },
      });
      await openSettings(user);
      await user.type(screen.getByLabelText("Name"), " draft");
      app.route({
        "GET /api/v1/printers/4": json({ ...base, edit_version: 7, name: "Other editor" }),
      });
      await act(async () => queryClient.invalidateQueries({ queryKey: ["printers", 4] }));
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      await screen.findByRole("button", { name: "Review current settings" });
      expect(sent[0]?.get("If-Match")).toBe(`"printer-4-e${base.edit_epoch}-v1"`);
      expect(sent[0]?.get("X-PrintStash-Edit-Contract")).toBe("conditional-v1");
      expect(screen.getByLabelText("Name")).toHaveValue("Voron draft");
    });

    it("adopts reviewed values without publishing the credential draft", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412) },
      });
      await openSettings(user);
      await user.type(screen.getByPlaceholderText("Unchanged"), "draft-only-credential");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      await screen.findByRole("button", { name: "Review current settings" });
      expect(
        JSON.stringify(
          queryClient
            .getQueryCache()
            .getAll()
            .map((query) => query.state.data),
        ),
      ).not.toContain("draft-only-credential");
      expect(
        JSON.stringify(
          queryClient
            .getMutationCache()
            .getAll()
            .map((mutation) => mutation.state.variables),
        ),
      ).not.toContain("draft-only-credential");
      app.route({
        "GET /api/v1/printers/4": json(
          aPrinter({ id: 4, name: "Reviewed printer", edit_version: 2 }),
        ),
      });
      await user.click(screen.getByRole("button", { name: "Review current settings" }));
      await user.click(await screen.findByRole("button", { name: "Use current values" }));
      expect(screen.getByLabelText("Name")).toHaveValue("Reviewed printer");
      expect(screen.getByPlaceholderText("Unchanged")).toHaveValue("");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("requires adoption when the reviewed connection uses another provider", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412) },
      });
      await openSettings(user);
      await user.type(screen.getByPlaceholderText("Unchanged"), "draft-only-credential");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      app.route({
        "GET /api/v1/printers/4": json(
          aPrinter({
            id: 4,
            provider: "octoprint",
            edit_version: 2,
            octoprint_url: "http://other.invalid",
          }),
        ),
      });
      await user.click(await screen.findByRole("button", { name: "Review current settings" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Save revised changes" })).toBeDisabled(),
      );
      expect(screen.getByPlaceholderText("Unchanged")).toHaveValue("draft-only-credential");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      await user.click(screen.getByRole("button", { name: "Use current values" }));
      expect(screen.getByPlaceholderText("Unchanged")).toHaveValue("");
      expect(screen.getByDisplayValue("http://other.invalid")).toBeVisible();
    });

    it("retires the inaccessible printer editor after review is denied", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412) },
      });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      app.route({ "GET /api/v1/printers/4": json({ detail: "printer_permission_denied" }, 403) });
      await user.click(await screen.findByRole("button", { name: "Review current settings" }));
      await waitFor(() => expect(screen.queryByLabelText("Name")).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
      expect(
        screen.queryByRole("button", { name: "Save revised changes" }),
      ).not.toBeInTheDocument();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("rejects a second conflict after reviewing current printer settings", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412) },
      });
      await openSettings(user);
      await user.type(screen.getByLabelText("Name"), " draft");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      app.route({ "GET /api/v1/printers/4": json(aPrinter({ id: 4, edit_version: 2 })) });
      await user.click(await screen.findByRole("button", { name: "Review current settings" }));
      await user.click(await screen.findByRole("button", { name: "Save revised changes" }));
      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(2));
      expect(screen.getByLabelText("Name")).toHaveValue("Voron draft");
      expect(
        screen.queryByRole("button", { name: "Save revised changes" }),
      ).not.toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "Save changes" })[0]).toBeDisabled();
    });

    it("validates the revised settings draft before sending it", async () => {
      const user = userEvent.setup();
      const app = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412) },
      });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      app.route({ "GET /api/v1/printers/4": json(aPrinter({ id: 4, edit_version: 2 })) });
      await user.click(await screen.findByRole("button", { name: "Review current settings" }));
      await user.clear(screen.getByLabelText("Name"));
      await user.click(await screen.findByRole("button", { name: "Save revised changes" }));
      expect(screen.getByLabelText("Name")).toBeInvalid();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("ignores a settings acknowledgement from a retired session", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderPrinter({ routes: { "PATCH /api/v1/printers/4": () => pending.promise } });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
      act(() => retirePrivateSessionScope());
      await act(async () =>
        pending.resolve(json(aPrinter({ id: 4, name: "Old session receipt", edit_version: 2 }))),
      );
      expect(
        screen.queryByRole("heading", { name: "Old session receipt" }),
      ).not.toBeInTheDocument();
      expect(
        JSON.stringify(
          queryClient
            .getQueryCache()
            .getAll()
            .map((query) => query.state.data),
        ),
      ).not.toContain("Old session receipt");
    });

    it("retires an in-flight printer settings review with its session", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "edit_conflict" }, 412) },
      });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      let reads = 0;
      app.route({
        "GET /api/v1/printers/4": () => {
          reads += 1;
          return reads === 1 ? pending.promise : json({ detail: "printer_permission_denied" }, 403);
        },
      });
      await user.click(await screen.findByRole("button", { name: "Review current settings" }));
      await waitFor(() => expect(reads).toBe(1));
      act(() => retirePrivateSessionScope());
      await act(async () =>
        pending.resolve(json(aPrinter({ id: 4, name: "Retired review", edit_version: 2 }))),
      );
      expect(screen.queryByText("Retired review")).not.toBeInTheDocument();
      expect(
        JSON.stringify(
          queryClient
            .getQueryCache()
            .getAll()
            .map((query) => query.state.data),
        ),
      ).not.toContain("Retired review");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("preserves a newer printer already observed while a save was pending", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderPrinter({ routes: { "PATCH /api/v1/printers/4": () => pending.promise } });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");
      await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
      act(() =>
        queryClient.setQueryData(
          ["printers", 4],
          aPrinter({ id: 4, name: "Newest printer", edit_version: 3 }),
        ),
      );
      await act(async () =>
        pending.resolve(json(aPrinter({ id: 4, name: "Older receipt", edit_version: 2 }))),
      );
      expect(screen.getByRole("heading", { name: "Newest printer" })).toBeVisible();
      expect(queryClient.getQueryData(["printers", 4])).toMatchObject({
        name: "Newest printer",
        edit_version: 3,
      });
    });

    it("names the credential the provider actually uses", async () => {
      // "API key" on a Bambu printer is the wrong thing to go looking for; it
      // wants the LAN access code printed on the machine.
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({ id: 4, name: "Voron", provider: "bambu_lan" }),
      });

      await openSettings(user);

      expect(screen.getByText("LAN access code")).toBeInTheDocument();
    });

    it("asks a Bambu printer for the serial it is addressed by", async () => {
      const user = userEvent.setup();
      renderPrinter({
        printer: aPrinter({ id: 4, name: "Voron", provider: "bambu_lan" }),
      });

      await openSettings(user);

      expect(screen.getByText("Printer serial")).toBeInTheDocument();
    });

    it("surfaces settings the server refused", async () => {
      const user = userEvent.setup();
      renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json({ detail: "printer_unreachable" }, 502) },
      });
      await openSettings(user);
      await user.type(screen.getByRole("textbox", { name: "Notes" }), "Service note");

      await user.click(screen.getAllByRole("button", { name: /Save changes/ })[0]);

      expect(await screen.findByText(/printer_unreachable/)).toBeInTheDocument();
    });
  });
  describe("the live snapshot", () => {
    /** Push a Moonraker `update` patch, which carries only what changed. */
    async function pushUpdate(data: PrinterSnapshot) {
      await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
      act(() => {
        FakeSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "update", data }) });
      });
    }

    it("keeps the fields an update did not mention", async () => {
      // A patch carries only what changed. Replacing the snapshot with it would
      // blank the filename and the duration on every temperature tick, which
      // arrives about once a second.
      renderPrinter();
      await screen.findByText("Voron");
      await pushSnapshot({
        print_stats: { state: "printing", filename: "bracket.gcode", print_duration: 60 },
      });

      await pushUpdate({ print_stats: { print_duration: 120 } });

      expect(await screen.findAllByText(/bracket\.gcode/)).not.toHaveLength(0);
    });

    it("distinguishes elapsed time from print time across sparse Klipper reports", async () => {
      renderPrinter();
      const job = within(await screen.findByRole("region", { name: "Current job" }));
      await pushSnapshot({
        print_stats: { state: "printing", filename: "bracket.gcode", total_duration: 12 },
        virtual_sdcard: { progress: 0.22 },
      });
      expect(job.getByText("Elapsed").parentElement).toHaveTextContent("12s");
      expect(job.queryByText("Print time")).not.toBeInTheDocument();

      await pushUpdate({ print_stats: { print_duration: 0 } });
      expect(job.getByText("Elapsed").parentElement).toHaveTextContent("12s");
      expect(job.getByText("Print time").parentElement).toHaveTextContent("0s");

      await pushUpdate({ print_stats: { print_duration: 2, total_duration: 17 } });
      expect(job.getByText("Elapsed").parentElement).toHaveTextContent("17s");
      expect(job.getByText("Print time").parentElement).toHaveTextContent("2s");

      await pushUpdate({ print_stats: { state: "paused", total_duration: 23 } });
      expect(job.getByText("Elapsed").parentElement).toHaveTextContent("23s");
      expect(job.getByText("Print time").parentElement).toHaveTextContent("2s");
      expect(job.queryByText("Total")).not.toBeInTheDocument();

      await pushSnapshot({ print_stats: { state: "printing", print_duration: 0 } });
      expect(job.queryByText("Elapsed")).not.toBeInTheDocument();
      expect(job.getByText("Print time").parentElement).toHaveTextContent("0s");
    });

    it("names the exact repeated Revision after a filename-less Moonraker transition", async () => {
      const remote = aPrinterFile({ model_name: "Calibration coupon" });
      const first = aPrintJob({
        id: 5,
        printer_id: 4,
        file_id: 20,
        model_id: 1,
        remote_filename: remote.remote_filename,
        state: "completed",
        finished_at: "2026-01-01T00:01:00Z",
      });
      const repeat = aPrintJob({
        ...first,
        id: 6,
        state: "started",
        finished_at: null,
      });
      let jobs = [first];
      renderPrinter({
        routes: {
          "GET /api/v1/printers/4/files": json([remote]),
          "GET /api/v1/printers/4/jobs": () => json(jobs),
        },
      });
      const current = within(await screen.findByRole("region", { name: "Current job" }));
      await pushSnapshot({
        print_stats: { state: "complete", filename: remote.remote_filename },
        virtual_sdcard: { progress: 1 },
      });
      expect(await current.findByText(/Last job · Calibration coupon/)).toBeVisible();

      jobs = [repeat, first];
      await pushUpdate({ print_stats: { state: "printing" }, virtual_sdcard: { progress: 0.15 } });

      expect(await current.findByText("Calibration coupon", { exact: true })).toBeVisible();
      expect(current.getByText(remote.remote_filename, { exact: true })).toBeVisible();
      expect(current.getByText("15%", { exact: true })).toBeVisible();
    });

    it("uses a different file reported by the printer instead of the managed filename", async () => {
      const managed = aPrinterFile({ model_name: "Calibration coupon" });
      renderPrinter({
        routes: {
          "GET /api/v1/printers/4/files": json([managed]),
          "GET /api/v1/printers/4/jobs": json([
            aPrintJob({
              printer_id: 4,
              file_id: 20,
              model_id: 1,
              remote_filename: managed.remote_filename,
              state: "printing",
            }),
          ]),
        },
      });
      await pushSnapshot({
        print_stats: { state: "printing", filename: "Calibration/bed-square.gcode" },
      });
      const current = within(await screen.findByRole("region", { name: "Current job" }));
      expect(current.getByText("Calibration/bed-square.gcode")).toBeVisible();
      expect(current.queryByText("Calibration coupon")).not.toBeInTheDocument();
    });

    it("leaves a new run unnamed when only other or nonrunning jobs have names", async () => {
      renderPrinter({
        routes: {
          "GET /api/v1/printers/4/jobs": json([
            aPrintJob({ id: 8, printer_id: 3, state: "printing", remote_filename: "other.gcode" }),
            aPrintJob({
              id: 7,
              printer_id: 4,
              state: "uploading",
              remote_filename: "upload.gcode",
            }),
            aPrintJob({ id: 6, printer_id: 4, state: "queued", remote_filename: "next.gcode" }),
            aPrintJob({
              id: 5,
              printer_id: 4,
              state: "completed",
              remote_filename: "old.gcode",
              finished_at: "2026-01-01T00:01:00Z",
            }),
          ]),
        },
      });
      await pushSnapshot({
        print_stats: { state: "complete", filename: "old.gcode" },
        virtual_sdcard: { progress: 1 },
      });
      await pushUpdate({ print_stats: { state: "printing" } });

      const current = within(await screen.findByRole("region", { name: "Current job" }));
      expect(current.getByText("Print name not reported")).toBeVisible();
      expect(await current.findByText("Up next: next.gcode")).toBeVisible();
      expect(current.queryByText(/old\.gcode|other\.gcode|upload\.gcode/)).not.toBeInTheDocument();
    });

    it("retains a zero target across a sparse heater patch", async () => {
      renderPrinter();
      await pushSnapshot({ extruder: { temperature: 22, target: 0 } });
      await pushUpdate({ extruder: { temperature: 31 } });

      expect(screen.getByText("31 °C", { exact: true })).toBeVisible();
      expect(screen.getByText("Target 0 °C", { exact: true })).toBeVisible();
    });

    it("distinguishes an unavailable fan from a reported stop", async () => {
      renderPrinter();
      await pushSnapshot({ fan: { speed: 0 } });
      expect(screen.getByText("0%", { exact: true })).toBeVisible();

      await pushUpdate({ fan: { speed: null } });
      expect(screen.queryByText("0%", { exact: true })).not.toBeInTheDocument();
    });

    it("retargets the fan rotor from reported output", async () => {
      renderPrinter();
      await pushSnapshot({ fan: { speed: 0 } });
      const fan = screen.getByRole("region", { name: "Part cooling fan" });
      const rotor = fan.querySelector("svg");
      expect(rotor).toHaveStyle({ transform: "rotate(0deg)" });

      await pushUpdate({ fan: { speed: 0.6 } });

      expect(rotor).toHaveStyle({ transform: "rotate(162deg)" });
      expect(within(fan).getByText("60%")).toBeVisible();
    });

    it("shows an absent Bambu SD card on the machine panel", async () => {
      renderPrinter({ printer: aPrinter({ id: 4, provider: "bambu_lan" }) });
      await pushSnapshot({ bambu_storage: { card_present: false } });
      const machine = within(screen.getByRole("region", { name: "Machine" }));
      expect(machine.getByText("Not inserted")).toBeVisible();
      expect(machine.queryByText("Chamber")).not.toBeInTheDocument();
    });

    it("displays Bambu chamber temperature from a standalone reading", async () => {
      renderPrinter({ printer: aPrinter({ id: 4, provider: "bambu_lan" }) });
      await pushSnapshot({ "temperature_sensor chamber": { temperature: 30 } });
      const machine = within(screen.getByRole("region", { name: "Machine" }));
      expect(machine.getByText("30 °C")).toBeVisible();
      expect(machine.queryByText("SD card")).not.toBeInTheDocument();
    });

    it("shows the Bambu time estimate only during the active job", async () => {
      renderPrinter({ printer: aPrinter({ id: 4, provider: "bambu_lan" }) });
      await pushSnapshot({ print_stats: { state: "printing", remaining_minutes: 37 } });
      const job = within(screen.getByRole("region", { name: "Current job" }));
      expect(job.getByText("37 min")).toBeVisible();
      await pushUpdate({ print_stats: { remaining_minutes: 0 } });
      expect(job.getByText("0 min")).toBeVisible();
      await pushUpdate({ print_stats: { state: "complete" } });
      expect(job.queryByText("Time remaining")).not.toBeInTheDocument();
    });

    it("keeps the Bambu layer total across a sparse layer update", async () => {
      renderPrinter({ printer: aPrinter({ id: 4, provider: "bambu_lan" }) });
      await pushSnapshot({
        print_stats: { state: "printing", external_current_layer: 0, external_total_layers: 120 },
      });
      const job = screen.getByRole("region", { name: "Current job" });
      expect(within(job).getByText("0 / 120")).toBeVisible();

      await pushUpdate({ print_stats: { external_current_layer: 8 } });

      expect(within(job).getByText("8 / 120")).toBeVisible();
    });

    it("drops the previous Bambu job metrics when a new print starts", async () => {
      renderPrinter({ printer: aPrinter({ id: 4, provider: "bambu_lan" }) });
      await pushSnapshot({
        print_stats: {
          state: "complete",
          filename: "old.gcode",
          external_current_layer: 120,
          external_total_layers: 120,
        },
        virtual_sdcard: { progress: 1 },
      });

      await pushUpdate({ print_stats: { state: "printing" } });

      const job = screen.getByRole("region", { name: "Current job" });
      expect(within(job).getByRole("button", { name: "Pause" })).toBeEnabled();
      expect(within(job).queryByText("old.gcode")).not.toBeInTheDocument();
      expect(within(job).queryByText("120 / 120")).not.toBeInTheDocument();
      expect(within(job).queryByText("100%")).not.toBeInTheDocument();
    });

    it("shows Ready only when the live Klipper controller reports it is idle", async () => {
      renderPrinter();
      const job = within(await screen.findByRole("region", { name: "Current job" }));
      expect(job.queryByText("Ready")).not.toBeInTheDocument();
      await pushSnapshot({ ...READY, webhooks: { state: "error" } });
      expect(job.queryByText("Ready")).not.toBeInTheDocument();
      await pushSnapshot({ ...READY, idle_timeout: { state: "Printing" } });
      expect(job.queryByText("Ready")).not.toBeInTheDocument();
      await pushSnapshot(READY);
      expect(job.getByText("Ready")).toBeVisible();
    });

    it("shows reported Klipper feeds beside the extrusion controls", async () => {
      renderPrinter({
        at: "/printers/4?tab=controls",
        routes: {
          "GET /api/v1/printers/4/material-state": json({
            tools: [],
            slots: [
              {
                slot_key: "manual:1",
                label: "Manual feed 1",
                source: "manual",
                state: "loaded",
                material_type: "PLA",
                stale: false,
              },
              {
                slot_key: "spoolman",
                label: "Tracked feed",
                source: "moonraker_spoolman",
                state: "loaded",
                spool_name: "Blue PETG",
                stale: true,
              },
              {
                slot_key: "manual:2",
                label: "Manual feed 2",
                source: "manual",
                state: "empty",
                material_type: "ABS",
                stale: false,
              },
            ],
          }),
        },
      });
      await pushSnapshot(READY);
      const filament = within(screen.getByRole("region", { name: "Filament" }));
      expect(await filament.findByText("PLA")).toBeVisible();
      expect(filament.getByText("Blue PETG")).toBeVisible();
      expect(filament.getByText(/Tracked feed.*Stale/)).toBeVisible();
      expect(filament.getByText("empty")).toBeVisible();
      expect(filament.queryByText("ABS")).not.toBeInTheDocument();
      expect(filament.getByRole("button", { name: "Feed 5 mm" })).toBeDisabled();
      expect(filament.getByRole("link", { name: "Materials & maintenance" })).toHaveAttribute(
        "href",
        "/printers/4?tab=materials",
      );
      expect(screen.queryByRole("region", { name: "Material setup" })).not.toBeInTheDocument();
    });

    it("keeps the Klipper feed summary read-only for viewers without reported slots", async () => {
      renderPrinter({
        printer: aPrinter({
          id: 4,
          access: printerAccess({ can_print: false, can_admin: false, can_control: false }),
        }),
      });
      await pushSnapshot(READY);
      const filament = within(screen.getByRole("region", { name: "Filament" }));
      expect(await filament.findByText("No material slots reported.")).toBeVisible();
      expect(filament.getByRole("button", { name: "Retract 5 mm" })).toBeDisabled();
      expect(filament.queryByRole("link")).not.toBeInTheDocument();
    });

    it("uses the Bambu machine facts when no motion data is available", async () => {
      renderPrinter({
        printer: aPrinter({
          id: 4,
          provider: "bambu_lan",
          model_name: "Bambu Lab P1S",
          capabilities: printerCapabilities({ can_send_gcode: false }),
        }),
        routes: {
          "GET /api/v1/printers/4/material-state": json({
            tools: [{ stale: false, nozzle_diameter_mm: 0.4 }],
            slots: [
              {
                slot_key: "ams-0",
                label: "AMS A1",
                state: "loaded",
                material_type: "PLA",
                color_hex: "#4269E1",
                stale: false,
              },
            ],
          }),
        },
      });

      expect(await screen.findByRole("heading", { name: "Machine" })).toBeVisible();
      expect(await screen.findByText("Bambu Lab P1S")).toBeVisible();
      expect(await screen.findByText("0.4 mm")).toBeVisible();
      expect(await screen.findByText("PLA", { exact: true })).toBeVisible();
      expect(screen.getByLabelText("#4269E1")).toHaveStyle({ backgroundColor: "#4269E1" });
      expect(screen.queryByText("X", { exact: true })).not.toBeInTheDocument();
    });

    it("shows only the four latest finished runs for the Bambu printer", async () => {
      const runs = [1, 5, 3, 2, 4].map((id) =>
        aPrintJob({
          id,
          printer_id: 4,
          remote_filename: `part-${id}.gcode`,
          state: id === 5 ? "failed" : "completed",
          finished_at: `2026-01-0${id}T01:00:00Z`,
        }),
      );
      renderPrinter({
        printer: aPrinter({ id: 4, provider: "bambu_lan" }),
        routes: {
          "GET /api/v1/printers/4/jobs": json([
            ...runs,
            aPrintJob({
              id: 6,
              printer_id: 5,
              remote_filename: "other.gcode",
              state: "completed",
              finished_at: "2026-01-06T01:00:00Z",
            }),
            aPrintJob({
              id: 7,
              printer_id: 4,
              remote_filename: "queued.gcode",
            }),
          ]),
        },
      });

      const history = within(await screen.findByRole("region", { name: "Print history" }));
      const rows = await history.findAllByRole("listitem");
      expect(rows).toHaveLength(4);
      for (const [index, id] of [5, 4, 3, 2].entries()) {
        expect(rows[index]).toHaveTextContent(`part-${id}.gcode`);
      }
      expect(rows[0]).toHaveTextContent("Failed");
    });

    it("omits the Bambu history summary when the only run is active", async () => {
      renderPrinter({
        printer: aPrinter({ id: 4, provider: "bambu_lan" }),
        routes: {
          "GET /api/v1/printers/4/jobs": json([aPrintJob({ printer_id: 4, state: "printing" })]),
        },
      });
      await pushSnapshot({ print_stats: { state: "printing", filename: "bracket.gcode" } });

      const current = screen.getByRole("region", { name: "Current job" });
      expect(within(current).getByText("bracket.gcode")).toBeVisible();
      await waitFor(() =>
        expect(screen.queryByRole("region", { name: "Print history" })).toBeNull(),
      );
      expect(screen.getByRole("tab", { name: "Jobs" })).toBeVisible();
    });

    it("keeps Bambu job history readable for viewers", async () => {
      renderPrinter({
        printer: aPrinter({
          id: 4,
          provider: "bambu_lan",
          access: printerAccess({
            role: "view",
            can_print: false,
            can_control: false,
            can_admin: false,
          }),
        }),
      });
      await pushSnapshot({ print_stats: { state: "standby" } });

      const current = within(screen.getByRole("region", { name: "Current job" }));
      expect(current.getByText("No active print")).toBeVisible();
      expect(current.queryByRole("button", { name: "Add to queue" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Printer settings" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Part cooling fan" })).not.toBeInTheDocument();

      const history = within(screen.getByRole("region", { name: "Print history" }));
      expect(history.getByRole("link", { name: "Jobs" })).toBeVisible();
      expect(await history.findByText("No print jobs yet.")).toBeVisible();
    });

    it("lays out reported AMS trays by unit with the external spool apart", async () => {
      const slots = [
        {
          slot_key: "ams:0:0",
          label: "AMS 0 tray 0",
          state: "loaded",
          material_type: "PLA",
          color_hex: "#4169E1",
        },
        {
          slot_key: "ams:0:1",
          label: "AMS 0 tray 1",
          state: "loaded",
          material_type: "PETG",
          color_hex: "#FFFFFF",
        },
        { slot_key: "ams:0:2", label: "AMS 0 tray 2", state: "empty" },
        { slot_key: "ams:0:3", label: "AMS 0 tray 3", state: "unknown" },
        { slot_key: "ams:2:0", label: "AMS 2 tray 0", state: "empty" },
        { slot_key: "external", label: "External spool", state: "empty" },
      ].map((slot) => ({ ...slot, source: "bambu_ams", stale: false }));
      renderPrinter({
        printer: aPrinter({ id: 4, provider: "bambu_lan" }),
        routes: { "GET /api/v1/printers/4/material-state": json({ tools: [], slots }) },
      });

      const bank = await screen.findByRole("region", { name: "AMS 0" });
      expect(within(bank).getAllByRole("listitem")).toHaveLength(4);
      expect(within(bank).getByText("PLA")).toBeVisible();
      expect(within(bank).getByLabelText("#4169E1")).toHaveStyle({ backgroundColor: "#4169E1" });
      expect(within(bank).getByText("unknown")).toBeVisible();
      expect(within(bank).queryByText("External spool")).not.toBeInTheDocument();
      expect(screen.getByRole("region", { name: "AMS 2" })).toBeVisible();
      expect(screen.getByText("External spool")).toBeVisible();
    });

    it("identifies both reported external Bambu inputs", async () => {
      const slots = ["external", "external:254"].map((slot_key) => ({
        slot_key,
        label: "External spool",
        state: "loaded",
        material_type: "PLA",
        source: "bambu_ams",
        stale: false,
      }));
      renderPrinter({
        printer: aPrinter({ id: 4, provider: "bambu_lan" }),
        routes: { "GET /api/v1/printers/4/material-state": json({ tools: [], slots }) },
      });
      expect(await screen.findByText("External spool 254")).toBeVisible();
      expect(screen.getByText("External spool 255")).toBeVisible();
    });

    it("clears live readings when the provider goes offline", async () => {
      renderPrinter();
      await pushSnapshot({ extruder: { temperature: 24.5 } });
      act(() => {
        FakeSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "offline" }) });
      });

      expect(screen.queryByText("24.5 °C", { exact: true })).not.toBeInTheDocument();
      expect(screen.getByText("Telemetry disconnected")).toBeVisible();
    });

    it("takes the new value the update carried", async () => {
      renderPrinter();
      await screen.findByText("Voron");
      await pushSnapshot({ print_stats: { state: "paused", filename: "bracket.gcode" } });

      await pushUpdate({ print_stats: { state: "printing" } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /Pause/ })).toBeInTheDocument(),
      );
    });
  });

  describe("controlling a print that will not obey", () => {
    it("says so when the printer refuses a pause", async () => {
      // The button going quiet reads as the click not registering, and the user
      // presses it again.
      const user = userEvent.setup();
      renderPrinter({
        routes: { "POST /api/v1/printers/4/pause": json({ detail: "printer_busy" }, 409) },
      });
      await screen.findByText("Voron");
      await pushSnapshot(PRINTING);

      await user.click(await screen.findByRole("button", { name: /Pause/ }));

      expect(await screen.findByText("Printer busy.")).toBeInTheDocument();
    });
  });
});

/** Connection cleanup takes effect before close events or deferred tickets can reconnect. */
describe("printer page connection lifetimes", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  async function settle() {
    await act(async () => vi.advanceTimersByTimeAsync(0));
  }

  it("does not reconnect a disposed printer page", async () => {
    const page = renderPrinter();
    await settle();
    expect(FakeSocket.instances).toHaveLength(1);
    page.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(
      page.requestsWithMethod("POST").filter(({ url }) => url.includes("ws-ticket")),
    ).toHaveLength(1);
  });

  it("rejects an abandoned printer page ticket", async () => {
    const ticket = Promise.withResolvers<Response>();
    const page = renderPrinter({
      routes: { "POST /api/v1/printers/4/ws-ticket": () => ticket.promise },
    });
    await settle();
    page.unmount();
    ticket.resolve(json({ ticket: "obsolete", expires_in: 60 }));
    await settle();
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("ignores old printer snapshot callbacks after a switch", async () => {
    const page = renderPrinter();
    await settle();
    const oldMessage = FakeSocket.latest?.onmessage;
    page.route({
      "GET /api/v1/printers/8/jobs": json([]),
      "GET /api/v1/printers/8/files": json([]),
      "GET /api/v1/printers/8/diagnostics": json({ provider: "moonraker", checks: [] }),
      "GET /api/v1/printers/8/config": json({ config: "" }),
      "POST /api/v1/printers/8/ws-ticket": json({ ticket: "fresh", expires_in: 60 }),
    });
    page.rerender(
      <PrinterDetailPage printerId={8} initialPrinter={aPrinter({ id: 8, name: "Prusa" })} />,
    );
    await settle();
    act(() =>
      oldMessage?.({
        data: JSON.stringify({
          type: "snapshot",
          data: { print_stats: { state: "printing", filename: "obsolete.gcode" } },
        }),
      }),
    );
    expect(screen.queryAllByText("obsolete.gcode")).toHaveLength(0);
    page.unmount();
  });

  it("reauthorizes the printer socket on a new login", async () => {
    const page = renderPrinter();
    await settle();
    const first = FakeSocket.instances[0];
    act(() => {
      clearLogin();
      storeLogin("", { id: 9, username: "next", email: null, is_superuser: false });
    });
    await settle();
    expect(first?.close).toHaveBeenCalledOnce();
    expect(FakeSocket.instances).toHaveLength(2);
    const current = FakeSocket.instances[1];
    act(() =>
      current?.onmessage?.({
        data: JSON.stringify({
          type: "snapshot",
          data: { print_stats: { state: "printing", filename: "current.gcode" } },
        }),
      }),
    );
    expect(screen.getAllByText("current.gcode")).not.toHaveLength(0);
    page.unmount();
  });

  it("stops printer callbacks on logout", async () => {
    const page = renderPrinter();
    await settle();
    const socket = FakeSocket.latest;
    act(() =>
      socket?.onmessage?.({
        data: JSON.stringify({
          type: "snapshot",
          data: { print_stats: { state: "printing", filename: "private.gcode" } },
        }),
      }),
    );
    expect(screen.getAllByText(/private.gcode/)).not.toHaveLength(0);
    act(() => clearLogin());
    expect(socket?.close).toHaveBeenCalledOnce();
    expect(screen.queryAllByText("private.gcode")).toHaveLength(0);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(
      page.requestsWithMethod("POST").filter(({ url }) => url.includes("ws-ticket")),
    ).toHaveLength(1);
    page.unmount();
  });
});

describe("PrinterDetailPage HTTP ownership", () => {
  it("revalidates an initially supplied printer", async () => {
    renderPrinter({
      routes: { "GET /api/v1/printers/4": json(aPrinter({ id: 4, name: "Fresh printer" })) },
    });
    expect(await screen.findByText("Fresh printer")).toBeInTheDocument();
  });

  it("aborts reads for a previous printer", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderPrinter({
      routes: {
        "GET /api/v1/printers/4/files": (_url, init) => {
          signal ??= init?.signal;
          return pending.promise;
        },
        "GET /api/v1/printers/5": json(aPrinter({ id: 5, name: "New machine" })),
        "GET /api/v1/printers/5/jobs": json([]),
        "GET /api/v1/printers/5/material-state": json({ slots: [], tools: [] }),
        "GET /api/v1/printers/5/files": json([
          aPrinterFile({ id: 51, printer_id: 5, remote_filename: "current.gcode" }),
        ]),
        "GET /api/v1/printers/5/diagnostics": json({ provider: "moonraker", checks: [] }),
        "GET /api/v1/printers/5/config": json({ config: "" }),
        "GET /api/v1/printers/5/heater-history": json(EMPTY_HEATER_HISTORY),
        "POST /api/v1/printers/5/ws-ticket": json({ ticket: "new", expires_in: 60 }),
      },
    });
    await waitFor(() =>
      expect(app.requestsWithMethod("GET").some((call) => call.url.includes("/4/files"))).toBe(
        true,
      ),
    );
    app.rerender(
      <PrinterDetailPage printerId={5} initialPrinter={aPrinter({ id: 5, name: "New machine" })} />,
    );
    expect(signal?.aborted).toBe(true);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Files" }));
    expect(
      await within(await screen.findByRole("table", { name: "Printer files" })).findByText(
        "current.gcode",
      ),
    ).toBeInTheDocument();
    await act(async () =>
      pending.resolve(json([aPrinterFile({ remote_filename: "obsolete.gcode" })])),
    );
    expect(screen.queryByText("obsolete.gcode")).not.toBeInTheDocument();
  });

  it("shares printer resources between mounted views", async () => {
    const printer = aPrinter({ id: 4, name: "Voron" });
    const app = renderApp(
      <>
        <PrinterDetailPage printerId={4} initialPrinter={printer} />
        <PrinterDetailPage printerId={4} initialPrinter={printer} />
      </>,
      {
        routes: {
          "GET /api/v1/printers/4": json(printer),
          "GET /api/v1/printers/4/jobs": json([]),
          "GET /api/v1/printers/4/files": json([]),
          "GET /api/v1/printers/4/diagnostics": json(aPrinterDiagnostics()),
          "GET /api/v1/printers/4/config": json({ config: "" }),
          "GET /api/v1/printers/4/material-state": json({ tools: [], slots: [] }),
          "GET /api/v1/printers/4/bed-mesh": json({ state: "unconfigured" }),
          "GET /api/v1/printers/4/heater-history": json(EMPTY_HEATER_HISTORY),
          "POST /api/v1/printers/4/ws-ticket": json({ ticket: "t", expires_in: 60 }),
        },
      },
    );
    await waitFor(() => expect(app.requestsWithMethod("GET")).toHaveLength(8));
    for (const path of [
      "/4/jobs",
      "/4/files",
      "/4/config",
      "/4/diagnostics",
      "/4/heater-history",
      "/4/bed-mesh",
      "/4/material-state",
    ])
      expect(app.requestsWithMethod("GET").filter((call) => call.url.includes(path))).toHaveLength(
        1,
      );
  });

  it.each([403, 404])("retires a loaded printer view after a %s denial", async (status) => {
    const app = renderPrinter();
    await screen.findByRole("heading", { name: "Voron" });
    await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
    const socket = FakeSocket.latest;
    app.route({ "GET /api/v1/printers/4": json({ detail: "printer_permission_denied" }, status) });
    await act(async () => queryClient.invalidateQueries({ queryKey: ["printers", 4] }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Voron" })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("tab", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
    expect(socket?.close).toHaveBeenCalled();
    expect(socket?.onmessage).toBeNull();
  });

  async function replaceStream(app: ReturnType<typeof renderPrinter>) {
    const previous = FakeSocket.latest;
    app.route({ "GET /api/v1/printers/4": json({ detail: "printer_permission_denied" }, 403) });
    await act(async () => queryClient.invalidateQueries({ queryKey: ["printers", 4] }));
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(previous?.onmessage).toBeNull();

    app.route({ "GET /api/v1/printers/4": json(aPrinter({ id: 4, name: "Voron" })) });
    await userEvent.click(retry);
    await waitFor(() => expect(FakeSocket.latest).not.toBe(previous));
    await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
  }

  it("releases print controls when a pause settles after stream retirement", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const app = renderPrinter({
      routes: { "POST /api/v1/printers/4/pause": () => pending.promise },
    });
    await pushSnapshot(PRINTING);
    await user.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() =>
      expect(app.requestsWithMethod("POST").some((call) => call.url.endsWith("/pause"))).toBe(true),
    );

    await replaceStream(app);
    await pushSnapshot({ ...READY, print_stats: { state: "paused" } });
    expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
    await act(async () => pending.resolve(json({ ok: true })));

    await waitFor(() => expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  it("releases machine controls without announcing a retired command receipt", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const app = renderPrinter({ routes: { "POST /api/v1/printers/4/jog": () => pending.promise } });
    await pushSnapshot(READY);
    await user.click(screen.getByRole("button", { name: "X+" }));
    await waitFor(() =>
      expect(app.requestsWithMethod("POST").some((call) => call.url.endsWith("/jog"))).toBe(true),
    );

    await replaceStream(app);
    await pushSnapshot(READY);
    await act(async () => pending.resolve(json({ ok: true })));

    await waitFor(() => expect(screen.getByRole("button", { name: "X+" })).toBeEnabled());
    expect(screen.queryByText("Move sent")).not.toBeInTheDocument();
  });

  it("recovers a denied printer after explicit retry", async () => {
    const app = renderPrinter({
      routes: {
        "GET /api/v1/printers/4": json({ detail: "printer_permission_denied" }, 403),
      },
    });
    const retry = await screen.findByRole("button", { name: "Retry" });
    app.route({ "GET /api/v1/printers/4": json(aPrinter({ id: 4, name: "Authorized again" })) });
    const ticketsBeforeRetry = app.requestsWithMethod("POST").length;
    await userEvent.setup().click(retry);
    expect(await screen.findByRole("heading", { name: "Authorized again" })).toBeVisible();
    await waitFor(() =>
      expect(app.requestsWithMethod("POST")).toHaveLength(ticketsBeforeRetry + 1),
    );
    await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
  });

  it("preserves settings drafts during revalidation", async () => {
    const user = userEvent.setup();
    renderPrinter();
    await user.click(await screen.findByRole("tab", { name: "Settings" }));
    const name = screen.getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Unsaved machine");
    await act(async () => queryClient.invalidateQueries({ queryKey: ["printers", 4] }));
    expect(name).toHaveValue("Unsaved machine");
  });

  it("refreshes printer resources after reconnect", async () => {
    const app = renderPrinter();
    await waitFor(() => expect(app.requestsWithMethod("GET")).toHaveLength(8));
    const first = FakeSocket.latest;
    await waitFor(() => expect(first?.onopen).not.toBeNull());
    act(() => first?.onopen?.());
    vi.useFakeTimers();
    try {
      act(() => first?.onclose?.());
      await act(async () => vi.advanceTimersByTimeAsync(3000));
      expect(FakeSocket.latest).not.toBe(first);
      await act(async () => FakeSocket.latest?.onopen?.());
      await act(async () => vi.advanceTimersByTimeAsync(0));
      for (const path of ["/4/jobs", "/4/files", "/4/config", "/4/diagnostics"])
        expect(
          app.requestsWithMethod("GET").filter((call) => call.url.includes(path)),
        ).toHaveLength(2);
      expect(
        app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/printers/4")),
      ).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects a file acknowledgement after a printer switch", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderPrinter({
      routes: {
        "POST /api/v1/printers/4/files/sync": (_url, init) => {
          signal = init?.signal;
          return pending.promise;
        },
        "GET /api/v1/printers/5": json(aPrinter({ id: 5, name: "Current machine" })),
        "GET /api/v1/printers/5/jobs": json([]),
        "GET /api/v1/printers/5/files": json([
          aPrinterFile({ printer_id: 5, remote_filename: "current.gcode" }),
        ]),
        "GET /api/v1/printers/5/diagnostics": json({ provider: "moonraker", checks: [] }),
        "GET /api/v1/printers/5/config": json({ config: "" }),
        "GET /api/v1/printers/5/heater-history": json(EMPTY_HEATER_HISTORY),
        "POST /api/v1/printers/5/ws-ticket": json({ ticket: "t", expires_in: 60 }),
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Files" }));
    await user.click(screen.getByRole("button", { name: "Sync printer files" }));
    await waitFor(() =>
      expect(app.requestsWithMethod("POST").some((call) => call.url.includes("files/sync"))).toBe(
        true,
      ),
    );
    app.rerender(
      <PrinterDetailPage
        printerId={5}
        initialPrinter={aPrinter({ id: 5, name: "Current machine" })}
      />,
    );
    expect(signal?.aborted).toBe(true);
    await user.click(await screen.findByRole("tab", { name: "Files" }));
    expect(
      await within(await screen.findByRole("table", { name: "Printer files" })).findByText(
        "current.gcode",
      ),
    ).toBeInTheDocument();
    await act(async () =>
      pending.resolve(json([aPrinterFile({ remote_filename: "obsolete.gcode" })])),
    );
    expect(screen.queryByText("obsolete.gcode")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync printer files" })).not.toBeDisabled();
  });

  it("surfaces printer lookup failure for retry", async () => {
    let reads = 0;
    renderApp(<PrinterDetailPage printerId={4} />, {
      routes: {
        "GET /api/v1/printers/4": () =>
          ++reads === 1
            ? json({ detail: "temporarily_unavailable" }, 503)
            : json(aPrinter({ id: 4, name: "Recovered machine" })),
        "GET /api/v1/printers/4/jobs": json([]),
        "GET /api/v1/printers/4/files": json([]),
        "GET /api/v1/printers/4/diagnostics": json(aPrinterDiagnostics()),
        "GET /api/v1/printers/4/config": json({ config: "" }),
        "GET /api/v1/printers/4/material-state": json({ tools: [], slots: [] }),
        "GET /api/v1/printers/4/bed-mesh": json({ state: "unconfigured" }),
        "GET /api/v1/printers/4/heater-history": json(EMPTY_HEATER_HISTORY),
        "POST /api/v1/printers/4/ws-ticket": json({ ticket: "t", expires_in: 60 }),
      },
    });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Recovered machine")).toBeInTheDocument();
    expect(reads).toBe(2);
  });

  it("refreshes printer resources after shared event resync", async () => {
    const app = renderPrinter();
    await waitFor(() => expect(app.requestsWithMethod("GET")).toHaveLength(8));
    await waitFor(() => expect(eventSocket.onmessage).not.toBeNull());
    await act(async () => eventSocket.onmessage?.({ data: JSON.stringify({ type: "resync" }) }));
    await waitFor(() => expect(app.requestsWithMethod("GET")).toHaveLength(13));
    expect(
      app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/printers/4")),
    ).toHaveLength(2);
  });

  it("coalesces print state job revalidation", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderPrinter({ routes: { "GET /api/v1/printers/4/jobs": () => pending.promise } });
    await pushSnapshot({ print_stats: { state: "printing" } });
    await pushSnapshot({ print_stats: { state: "paused" } });
    expect(
      app.requestsWithMethod("GET").filter((call) => call.url.includes("/4/jobs")),
    ).toHaveLength(1);
    await act(async () => pending.resolve(json([])));
  });

  it("publishes confirmed files over obsolete reads", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderPrinter({
      routes: {
        "GET /api/v1/printers/4/files": (_url, init) => {
          signal ??= init?.signal;
          return pending.promise;
        },
        "POST /api/v1/printers/4/files/sync": json([
          aPrinterFile({ remote_filename: "confirmed.gcode" }),
        ]),
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Files" }));
    await user.click(screen.getByRole("button", { name: "Sync printer files" }));
    expect(
      await within(await screen.findByRole("table", { name: "Printer files" })).findByText(
        "confirmed.gcode",
      ),
    ).toBeInTheDocument();
    expect(signal?.aborted).toBe(true);
    await act(async () =>
      pending.resolve(json([aPrinterFile({ remote_filename: "obsolete.gcode" })])),
    );
    expect(screen.queryByText("obsolete.gcode")).not.toBeInTheDocument();
    expect(
      app.requestsWithMethod("GET").filter((call) => call.url.includes("/4/files")),
    ).toHaveLength(1);
  });

  it("retains files after a denied sync", async () => {
    const app = renderPrinter({
      routes: {
        "GET /api/v1/printers/4/files": json([aPrinterFile()]),
        "POST /api/v1/printers/4/files/sync": json({ detail: "forbidden" }, 403),
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Files" }));
    expect(
      await within(await screen.findByRole("table", { name: "Printer files" })).findByText(
        "bracket.gcode",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sync printer files" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Sync printer files" })).not.toBeDisabled(),
    );
    expect(
      within(screen.getByRole("table", { name: "Printer files" })).getByText("bracket.gcode"),
    ).toBeInTheDocument();
    expect(
      app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/printers/4")),
    ).toHaveLength(1);
  });

  it("retires printer HTTP data on scope changes", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderPrinter({
      routes: {
        "GET /api/v1/printers/4/files": (_url, init) => {
          signal ??= init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() =>
      expect(app.requestsWithMethod("GET").some((call) => call.url.includes("/4/files"))).toBe(
        true,
      ),
    );
    act(() => retirePrivateSessionScope());
    expect(signal?.aborted).toBe(true);
    await waitFor(() =>
      expect(
        app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/printers/4")),
      ).toHaveLength(2),
    );
    await act(async () => pending.resolve(json([])));
  });
});
