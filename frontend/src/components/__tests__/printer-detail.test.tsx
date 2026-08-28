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
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrinterDetailPage } from "@/components/printer-detail";
import { aPrinter, printerAccess, printerCapabilities } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { PrinterFileRead, PrinterRead } from "@/types";

/** The subset of a live snapshot these tests push, named so it is not a dictionary. */
interface LiveSnapshot {
  print_stats?: {
    state?: string;
    filename?: string;
    print_duration?: number;
    total_duration?: number;
  };
}

/**
 * jsdom has no WebSocket, and the live snapshot is the *only* source of the
 * print state the controls key off — so a socket that merely exists is not
 * enough. This one records itself so a test can push the snapshot the printer
 * would have sent.
 */
class FakeSocket {
  static latest: FakeSocket | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  readyState = 1;

  constructor() {
    FakeSocket.latest = this;
  }

  close() {}
  send() {}
}

/** Push the snapshot the printer would have sent over the live socket. */
async function pushSnapshot(data: LiveSnapshot) {
  await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
  act(() => {
    FakeSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "snapshot", data }) });
  });
}

function aPrinterFile(over: Partial<PrinterFileRead> = {}): PrinterFileRead {
  return {
    id: 50,
    printer_id: 4,
    printer_name: "Voron",
    file_id: 20,
    model_id: 1,
    model_name: "Bracket",
    original_filename: "bracket.gcode",
    remote_filename: "bracket.gcode",
    size_bytes: 4096,
    sha256: "b".repeat(64),
    matched_by: "sha256",
    modified_at: "2026-01-01T00:00:00Z",
    last_seen_at: "2026-01-01T00:00:00Z",
    missing_since: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
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

function renderPrinter(options: RenderAppOptions & { printer?: PrinterRead } = {}) {
  const { printer = aPrinter({ id: 4, name: "Voron" }), routes = {}, ...rest } = options;
  return renderApp(<PrinterDetailPage printerId={4} initialPrinter={printer} />, {
    routes: {
      "GET /api/v1/printers/4": json(printer),
      "GET /api/v1/printers/4/jobs": json([]),
      "GET /api/v1/printers/4/files": json([]),
      "GET /api/v1/printers/4/status": json({ state: "ready" }),
      "GET /api/v1/printers/4/diagnostics": json({ provider: "moonraker", checks: [] }),
      "GET /api/v1/printers/4/config": json({ config: "" }),
      // The live socket is opened against a short-lived ticket, so the page
      // cannot reach a snapshot at all without this one.
      "POST /api/v1/printers/4/ws-ticket": json({ ticket: "t", expires_in: 60 }),
      "GET /api/v1/printers/4/materials": json({ slots: [], tools: [] }),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
  FakeSocket.latest = null;
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrinterDetailPage", () => {
  describe("what it shows", () => {
    it("names the printer", async () => {
      renderPrinter();

      expect(await screen.findByText("Voron")).toBeInTheDocument();
    });

    it("reports there is nothing printing", async () => {
      renderPrinter();

      expect(await screen.findByText("No active print")).toBeInTheDocument();
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

      expect(await screen.findByText("Printer files")).toBeInTheDocument();
    });

    it("shows the print history", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await screen.findByText("Voron");

      await user.click(screen.getByRole("tab", { name: "Jobs" }));

      expect(await screen.findByText("Print history")).toBeInTheDocument();
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

      // Pause, resume and cancel all relabel, so every control says the same thing.
      const blocked = await screen.findAllByRole("button", { name: /No access/ });
      expect(blocked.every((button) => button.hasAttribute("disabled"))).toBe(true);
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

      expect(await screen.findByRole("button", { name: /Pause/ })).toBeDisabled();
    });

    it("withholds pause from a printer that is not printing", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot({ print_stats: { state: "paused" } });

      expect(await screen.findByRole("button", { name: /Pause/ })).toBeDisabled();
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

      await pushSnapshot(PRINTING);

      expect(await screen.findByText("Current print")).toBeInTheDocument();
    });
  });

  describe("setting a temperature", () => {
    it("sends the hotend target the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      const inputs = screen.getAllByPlaceholderText("°C");
      await user.type(inputs[0], "215");
      await user.click(screen.getAllByRole("button", { name: /Set/ })[0]);

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

      await user.type(screen.getAllByPlaceholderText("°C")[1], "60");
      await user.click(screen.getAllByRole("button", { name: /Set/ })[1]);

      await waitFor(() =>
        expect(JSON.parse(temperatureBody(requestsWithMethod))).toMatchObject({
          heater: "bed",
          target: 60,
        }),
      );
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

      await user.click(screen.getByRole("button", { name: "PETG" }));

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
      expect(screen.queryByRole("button", { name: "PETG" })).toBeNull();
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
      expect(screen.getByRole("button", { name: "PETG" })).toBeDisabled();
    });
  });

  describe("the emergency stop", () => {
    it("asks before halting the printer", async () => {
      // It requires a firmware restart afterwards, so a misclick costs a print
      // and a trip to the machine.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter();
      await screen.findByText("Voron");

      await user.click(screen.getByRole("button", { name: /Emergency stop|E-Stop/i }));

      expect(requestsWithMethod("POST").some((call) => call.url.includes("emergency_stop"))).toBe(
        false,
      );
    });

    it("halts the printer once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/emergency_stop": json(null, 204) },
      });
      await screen.findByText("Voron");
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
      await screen.findByText("Printer files");
    }

    it("lists what is on the machine", async () => {
      const user = userEvent.setup();
      renderPrinter({ routes: { "GET /api/v1/printers/4/files": json([aPrinterFile()]) } });

      await openFiles(user);

      expect(await screen.findByText("bracket.gcode")).toBeInTheDocument();
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

    it("saves the details the operator changed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "PATCH /api/v1/printers/4": json(aPrinter({ id: 4, name: "Voron 2.4" })) },
      });
      await openSettings(user);
      const name = screen.getByLabelText("Name");
      await user.clear(name);
      await user.type(name, "Voron 2.4");

      await user.click(screen.getByRole("button", { name: /Save changes/ }));

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

      await user.click(screen.getByRole("button", { name: /Save changes/ }));

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

      await user.click(screen.getByRole("button", { name: /Save changes/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          api_key: "not-a-real-key",
        }),
      );
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

      await user.click(screen.getByRole("button", { name: /Save changes/ }));

      expect(await screen.findByText(/printer_unreachable/)).toBeInTheDocument();
    });
  });
  describe("the live snapshot", () => {
    /** Push a Moonraker `update` patch, which carries only what changed. */
    async function pushUpdate(data: LiveSnapshot) {
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
