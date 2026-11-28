/*
 * Adding a printer, which is where five providers' credential sets collide in
 * one form.
 *
 * One `Printer` row carries the fields for every provider, so the form is a set
 * of branches over one shape — and the failure mode is *mixing*: submitting
 * PrusaLink digest credentials alongside an empty Moonraker URL, or an Elegoo
 * Neptune 4 as its own provider rather than as a Moonraker variant. Every one of
 * those inserts happily and produces a printer that cannot connect, with an error
 * that names a transport rather than a wrong field.
 *
 * The double-submit case is separate and worth its own test: adding a printer is
 * not idempotent, so a second click before the request resolves creates two.
 *
 * The card half is about the display *preference* being honoured while a page is
 * already mounted. A preference read only at mount means the user toggles printer
 * artwork in settings, comes back, and nothing changed until a reload.
 *
 * Model detection has three outcomes and all three are here, because the fallback
 * is the one users hit: a printer whose model we cannot detect must still be
 * nameable, or the fleet view shows an unlabelled machine forever.
 */

import { anEditingBase } from "@/test-support/factories";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TestRouter } from "@/test-support/router";

import { PrintersPage } from "@/components/printers-list";
import { writePrinterCardImagePreference } from "@/lib/printer-card-display";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { queryKeys } from "@/lib/query-client";
import type {
  FleetSummary,
  PrintJobRead,
  PrinterCreate,
  PrinterRead,
  PrinterUpdate,
} from "@/types";

/**
 * The page runs against its real collaborators here: the real query hooks over
 * a pre-seeded cache, the real api client, the real auth context and router.
 * Only `fetch` is stood in for, which is what lets these tests pin the exact
 * HTTP request each setup form produces — the contract the backend router reads.
 */

const fetchMock = vi.fn<typeof fetch>();

function printerResponse(printer: PrinterRead): Response {
  return new Response(JSON.stringify(printer), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

/** Requests the api client sent with the given verb, oldest first. */
function requestsWithMethod(method: string) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([input, init]) => ({ url: String(input), body: String(init?.body ?? "") }));
}

const EMPTY_FLEET_SUMMARY: FleetSummary = {
  total_printers: 0,
  queued_jobs: 0,
  active_jobs: 0,
  draining_printers: 0,
  maintenance_printers: 0,
  attention_jobs: 0,
  printers: [],
};

const ADMIN_AUTH: AuthState = {
  user: { id: 1, username: "admin", email: null, is_superuser: true },
  loading: false,
  login: vi.fn<AuthState["login"]>(),
  logout: vi.fn<AuthState["logout"]>(),
  refresh: vi.fn<AuthState["refresh"]>(),
};
const MEMBER_AUTH: AuthState = {
  ...ADMIN_AUTH,
  user: { id: 2, username: "member", email: null, is_superuser: false },
};

/** FleetQueuePanel's history window is part of its query key. */
const FLEET_QUEUE_HISTORY_LIMIT = 20;

function renderPrintersPage(
  seed: {
    printers?: PrinterRead[];
    summary?: FleetSummary;
    jobs?: PrintJobRead[];
    auth?: AuthState;
  } = {},
) {
  const client = new QueryClient({
    defaultOptions: {
      // Seeded data must stay put: nothing here may fall back to the network.
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
    },
  });
  client.setQueryData(queryKeys.printers, seed.printers ?? []);
  client.setQueryData([...queryKeys.fleetQueue, FLEET_QUEUE_HISTORY_LIMIT], seed.jobs ?? []);
  client.setQueryData(queryKeys.fleetSummary, seed.summary ?? EMPTY_FLEET_SUMMARY);

  return render(
    <TestRouter>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={seed.auth ?? ADMIN_AUTH}>
          <PrintersPage />
        </AuthContext.Provider>
      </QueryClientProvider>
    </TestRouter>,
  );
}

/** Opens a printer card's context menu, as a right click does. */
function openCardMenu(name: string) {
  const card = screen.getByRole("link", { name }).closest("article");
  expect(card).not.toBeNull();
  fireEvent.contextMenu(card!);
}

async function chooseCardAction(name: string, action: string) {
  openCardMenu(name);
  await userEvent.click(await screen.findByRole("menuitem", { name: action }));
}

function makePrinter(overrides: Partial<PrinterRead> = {}): PrinterRead {
  return {
    ...anEditingBase(),
    provider_material_sync_enabled: true,
    operator_release_required: false,
    id: 1,
    name: "Voron 2.4",
    provider: "moonraker",
    moonraker_url: "http://10.0.0.1:7125",
    has_bambu_access_code: false,
    has_prusalink_password: false,
    has_prusalink_api_key: false,
    has_elegoo_centauri_access_code: false,
    has_octoprint_api_key: false,
    has_api_key: false,
    access: { role: "admin", can_view: true, can_print: true, can_control: true, can_admin: true },
    capabilities: {
      can_start: true,
      can_pause: true,
      can_resume: true,
      can_cancel: true,
      can_live_status: true,
      can_upload: true,
      can_list_files: true,
      can_send_gcode: true,
      can_measure_consumption: true,
      accepted_print_formats: ["gcode_text"],
      support_level: "stable",
      support_notes: [],
      unsupported_actions: [],
    },
    notes: null,
    group: null,
    is_default: false,
    drain_mode: false,
    drain_reason: null,
    drain_updated_at: null,
    status: "ready",
    last_seen_at: null,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation((input, init) => {
    if (!init?.method || init.method === "GET") {
      const body = String(input).includes("/fleet/summary") ? EMPTY_FLEET_SUMMARY : [makePrinter()];
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    return Promise.resolve(printerResponse(makePrinter()));
  });

  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openForm() {
  renderPrintersPage();
  await userEvent.click(screen.getByRole("button", { name: /add printer/i }));
}

describe("PrinterSetupForm", () => {
  it("names the Add printer dialog", async () => {
    await openForm();
    expect(screen.getByRole("dialog", { name: "Add printer" })).toBeVisible();
  });

  it("suggests an IP address for the printer", async () => {
    await openForm();
    expect(screen.getByLabelText("Moonraker URL")).toHaveAttribute(
      "placeholder",
      "http://192.168.1.50:7125",
    );
  });

  it("rejects an address that is only a scheme instead of saving it", async () => {
    await openForm();
    const dialog = screen.getByRole("dialog", { name: "Add printer" });
    await userEvent.type(within(dialog).getByLabelText("Name"), "Ender");
    await userEvent.type(screen.getByLabelText("Moonraker URL"), "://");

    await userEvent.click(within(dialog).getByRole("button", { name: "Add printer" }));

    expect(
      await within(dialog).findByText("Enter an address like http://192.168.1.20:7125"),
    ).toBeVisible();
    expect(screen.getByLabelText("Moonraker URL")).toHaveAttribute("aria-invalid", "true");
  });

  it("explains that .local names may not resolve from the container", async () => {
    await openForm();
    expect(screen.getByLabelText("Moonraker URL")).toHaveAccessibleDescription(
      "Use the printer's IP address. Names ending in .local usually can't be resolved from the PrintStash container.",
    );
  });

  it("submits only once when add is triggered twice before request resolves", async () => {
    let resolveCreate!: () => void;
    fetchMock.mockImplementation((input, init) =>
      init?.method === "POST"
        ? new Promise<Response>((resolve) => {
            resolveCreate = () => resolve(printerResponse(makePrinter()));
          })
        : Promise.resolve(
            new Response(
              JSON.stringify(
                String(input).includes("/fleet/summary") ? EMPTY_FLEET_SUMMARY : [makePrinter()],
              ),
              { status: 200, headers: { "content-type": "application/json" } },
            ),
          ),
    );
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Voron");
    await userEvent.type(screen.getByLabelText("Moonraker URL"), "http://voron.local:7125");
    const form = screen.getByLabelText("Name").closest("form")!;

    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(requestsWithMethod("POST")).toHaveLength(1);
    resolveCreate();
    await waitFor(() => expect(screen.queryByText("Adding...")).not.toBeInTheDocument());
  });

  it("submits PrusaLink Digest credentials without mixing provider fields", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Prusa MK4");
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "prusalink");
    await userEvent.type(screen.getByLabelText("PrusaLink URL"), "http://mk4.local");
    await userEvent.type(screen.getByLabelText("Password"), "secret");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    const posted = requestsWithMethod("POST")[0]!;
    expect(posted.url).toBe("/api/v1/printers");
    const payload: PrinterCreate = JSON.parse(posted.body);
    expect(payload).toMatchObject({
      name: "Prusa MK4",
      provider: "prusalink",
      prusalink_url: "http://mk4.local",
      prusalink_auth_mode: "digest",
      prusalink_username: "maker",
      prusalink_password: "secret",
    });
    expect(payload).not.toHaveProperty("moonraker_url");
  });

  it("maps Elegoo Neptune 4 setup to Moonraker variant", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Neptune 4 Max");
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "elegoo_neptune4");
    await userEvent.type(screen.getByLabelText("Printer URL"), "http://neptune.local:7125");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    const payload: PrinterCreate = JSON.parse(requestsWithMethod("POST")[0]!.body);
    expect(payload).toMatchObject({
      provider: "moonraker",
      provider_variant: "elegoo_neptune4",
      moonraker_url: "http://neptune.local:7125",
    });
  });

  it("explains that Centauri Carbon commands need the Mainboard ID while idle", async () => {
    await openForm();
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "elegoo_centauri_carbon");

    expect(screen.getByLabelText(/Mainboard ID/i)).toBeInTheDocument();
    expect(
      screen.getByText("Needed for reliable printer commands while idle, paused, or errored."),
    ).toBeInTheDocument();
  });

  it("submits Centauri Carbon 2 local MQTT credentials", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Centauri Carbon 2");
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "elegoo_centauri_carbon_2");
    expect(screen.getAllByText(/enable lan only/i)).toHaveLength(2);
    await userEvent.type(screen.getByLabelText("Printer host or IP"), "192.168.1.51");
    await userEvent.type(screen.getByLabelText("Printer access code"), "ABC123");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    const payload: PrinterCreate = JSON.parse(requestsWithMethod("POST")[0]!.body);
    expect(payload).toMatchObject({
      provider: "elegoo_centauri",
      provider_variant: "elegoo_centauri_carbon_2",
      elegoo_centauri_host: "192.168.1.51",
      elegoo_centauri_access_code: "ABC123",
    });
  });

  it("submits OctoPrint URL and API key", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "OctoPi");
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "octoprint");
    await userEvent.type(screen.getByLabelText("OctoPrint URL"), "http://octopi.local");
    await userEvent.type(screen.getByLabelText("API key"), "secret-key");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    const payload: PrinterCreate = JSON.parse(requestsWithMethod("POST")[0]!.body);
    expect(payload).toMatchObject({
      provider: "octoprint",
      octoprint_url: "http://octopi.local",
      octoprint_api_key: "secret-key",
    });
  });
});

describe("PrinterCard", () => {
  it("switches to global queue empty state", async () => {
    renderPrintersPage();
    await userEvent.click(screen.getByRole("tab", { name: "Queue" }));
    const queue = within(screen.getByRole("region", { name: "Queued print jobs" }));
    expect(queue.getByText("The queue is empty")).toBeVisible();
    expect(queue.getByRole("button", { name: "Add to queue" })).toBeEnabled();
  });

  it("summarizes fleet health and filters by printer group", async () => {
    renderPrintersPage({
      printers: [
        makePrinter({ id: 1, name: "Workshop Voron", group: "Workshop", status: "printing" }),
        makePrinter({ id: 2, name: "Garage Prusa", group: "Garage", status: "offline" }),
      ],
      summary: { ...EMPTY_FLEET_SUMMARY, total_printers: 2, active_jobs: 1 },
    });

    expect(screen.getByLabelText("Fleet summary")).toHaveTextContent("1");
    // DOM implementations differ on whether adjacent text nodes insert a space.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Group" }),
      screen.getByRole("option", { name: "Workshop" }),
    );
    expect(screen.getByRole("link", { name: "Workshop Voron" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Garage Prusa" })).not.toBeInTheDocument();
  });

  it("groups the printers that match the fleet health filter", async () => {
    renderPrintersPage({
      printers: [
        makePrinter({ id: 1, name: "Lab Voron", group: "Lab", status: "error" }),
        makePrinter({ id: 2, name: "Workshop Prusa", group: "Workshop", status: "ready" }),
        makePrinter({ id: 3, name: "Standalone printer", group: " ", status: "offline" }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Group printers" }));
    expect(
      within(screen.getByRole("region", { name: "Ungrouped" })).getByRole("link", {
        name: "Standalone printer",
      }),
    ).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /Needs attention/ }));
    expect(
      within(screen.getByRole("region", { name: "Lab" })).getByRole("link", {
        name: "Lab Voron",
      }),
    ).toBeVisible();
    expect(screen.queryByRole("region", { name: "Workshop" })).not.toBeInTheDocument();
  });

  it("presents the scoped fleet workload in each printer card", () => {
    renderPrintersPage({
      printers: [makePrinter({ id: 1, status: "printing" })],
      summary: {
        ...EMPTY_FLEET_SUMMARY,
        active_jobs: 1,
        queued_jobs: 1,
        printers: [
          {
            printer_id: 1,
            name: "Voron 2.4",
            group: null,
            status: "printing",
            current_job_id: 12,
            current_job_name: "current.gcode",
            current_priority: "normal",
            progress: 0.42,
            next_job_id: 14,
            next_job_name: "next.gcode",
            next_priority: "normal",
            drain_mode: false,
            maintenance: false,
            pending_operator_release: true,
            loaded_slots: ["AMS 0 tray 0 · PLA · #D42C2C"],
            loaded_material_slots: [
              {
                slot_key: "ams0-tray0",
                label: "AMS 0 tray 0 · front",
                material_type: "PLA",
                material_brand: null,
                spool_filament_id: null,
                color_hex: "#D42C2C",
              },
            ],
            nozzle_diameter_mm: 0.4,
          },
        ],
      },
    });

    expect(screen.getByText("current.gcode")).toBeVisible();
    expect(screen.getByText("42%")).toBeVisible();
    expect(screen.getByText("next.gcode")).toBeVisible();
    expect(screen.getByText("Operator release required")).toBeVisible();
    expect(screen.getByText("PLA")).toBeVisible();
    expect(screen.getByTitle("#D42C2C")).toHaveTextContent("AMS 0 tray 0 · front");
    expect(screen.queryByText(/#D42C2C/)).not.toBeInTheDocument();
    expect(screen.queryByText("Part cooling fan")).not.toBeInTheDocument();
  });

  it("shows only observed cooldown readings", () => {
    const printer = makePrinter();
    renderPrintersPage({
      printers: [printer],
      summary: {
        ...EMPTY_FLEET_SUMMARY,
        total_printers: 1,
        printers: [
          {
            printer_id: printer.id,
            name: printer.name,
            status: "ready",
            group: null,
            progress: null,
            loaded_slots: ["Front feed"],
            nozzle_diameter_mm: null,
            current_job_id: null,
            current_job_name: null,
            current_priority: null,
            next_job_id: null,
            next_job_name: null,
            next_priority: null,
            drain_mode: false,
            maintenance: false,
            pending_operator_release: false,
            telemetry: { nozzle_c: 72, bed_c: null, part_fan_speed: 0 },
          },
        ],
      },
    });

    expect(screen.getByText("No active print")).toBeVisible();
    expect(screen.getByText("Nozzle").parentElement).toHaveTextContent("72 °C");
    expect(screen.getByText("Part cooling fan").parentElement).toHaveTextContent("0%");
    expect(screen.queryByText("Bed", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText("Front feed")).toBeVisible();
  });

  it("filters the fleet from the attention count", async () => {
    renderPrintersPage({
      printers: [
        makePrinter({ id: 1, name: "Available Voron", status: "ready" }),
        makePrinter({ id: 2, name: "Draining Voron", status: "ready", drain_mode: true }),
      ],
    });

    const filter = screen.getByRole("button", { name: /Needs attention/ });
    await userEvent.click(filter);
    expect(filter).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("link", { name: "Draining Voron" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Available Voron" })).not.toBeInTheDocument();
  });

  it("omits artwork when the preference explicitly disables it", () => {
    window.localStorage.setItem("printstash.printer-card.show-image", "false");
    const plain = renderPrintersPage({
      printers: [makePrinter({ model_name: "Bambu Lab P1S" })],
    });
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    plain.unmount();

    window.localStorage.setItem("printstash.printer-card.show-image", "true");
    renderPrintersPage({ printers: [makePrinter({ model_name: "Voron 2.4" })] });
    expect(screen.getByAltText("Voron 2.4 printer")).toBeInTheDocument();
  });

  it("defaults to local FDM artwork for an unknown model", () => {
    renderPrintersPage({ printers: [makePrinter()] });
    expect(screen.getByAltText("Printer")).toHaveAttribute(
      "src",
      "/images/printers/generic-fdm.png",
    );
  });

  it("loads the exact P1S cover only after enabling model artwork", () => {
    renderPrintersPage({ printers: [makePrinter({ model_name: "Bambu Lab P1S" })] });
    expect(screen.getByRole("img", { name: "Bambu Lab P1S printer" })).toHaveAttribute(
      "src",
      "/images/printers/generic-fdm.png",
    );

    act(() => writePrinterCardImagePreference(true));

    expect(screen.getByAltText("Bambu Lab P1S printer")).toHaveAttribute(
      "src",
      expect.stringContaining("/BBL/Bambu%20Lab%20P1S_cover.png"),
    );
    fireEvent.error(screen.getByAltText("Bambu Lab P1S printer"));
    expect(screen.getByAltText("Bambu Lab P1S printer")).toHaveAttribute(
      "src",
      "/images/printers/generic-fdm.png",
    );
  });

  it("lets the user pick a model from the list when nothing was detected", async () => {
    const user = userEvent.setup();
    renderPrintersPage({ printers: [makePrinter()] });

    await chooseCardAction("Voron 2.4", "Set model");
    const picker = screen.getByRole("dialog", { name: "Choose printer model" });
    await user.type(within(picker).getByRole("searchbox"), "Voron 2.4");
    await user.click(within(picker).getByRole("button", { name: "Voron 2.4" }));

    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    const patched = requestsWithMethod("PATCH")[0]!;
    expect(patched.url).toBe("/api/v1/printers/1");
    const payload: PrinterUpdate = JSON.parse(patched.body);
    expect(payload).toEqual({ model_name: "Voron 2.4" });
  });

  it("reviews a competing quick model edit inside its dialog", async () => {
    const user = userEvent.setup();
    const original = makePrinter();
    let latest = makePrinter({ model_name: "Other model", edit_version: 2 });
    let patches = 0;
    const headers: Headers[] = [];
    const changes: PrinterUpdate[] = [];
    fetchMock.mockImplementation((input, init) => {
      const url = String(input);
      if (init?.method === "PATCH") {
        headers.push(new Headers(init.headers));
        changes.push(JSON.parse(String(init.body)));
        patches += 1;
        if (patches === 1)
          return Promise.resolve(
            new Response(JSON.stringify({ detail: "edit_conflict" }), { status: 412 }),
          );
        latest = { ...latest, model_name: "Homebrew CoreXY", edit_version: 3 };
        return Promise.resolve(printerResponse(latest));
      }
      const value = url.endsWith("/fleet/summary")
        ? EMPTY_FLEET_SUMMARY
        : url.endsWith("/printers")
          ? [latest]
          : latest;
      return Promise.resolve(
        new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } }),
      );
    });
    renderPrintersPage({ printers: [original] });
    await chooseCardAction("Voron 2.4", "Set model");
    await user.click(screen.getByRole("button", { name: "Use custom model" }));
    const dialog = screen.getByRole("dialog", { name: "Custom printer model" });
    await user.type(within(dialog).getByRole("textbox", { name: "Manufacturer" }), "Homebrew");
    await user.type(within(dialog).getByRole("textbox", { name: "Model" }), "CoreXY");
    await user.click(within(dialog).getByRole("button", { name: "Use this model" }));
    await user.click(
      await within(dialog).findByRole("button", { name: "Review current settings" }),
    );
    expect(await within(dialog).findByText("Other model")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Use this model" })).toBeDisabled();
    expect(within(dialog).getByRole("textbox", { name: "Manufacturer" })).toHaveValue("Homebrew");
    expect(within(dialog).getByRole("textbox", { name: "Model" })).toHaveValue("CoreXY");
    expect(patches).toBe(1);
    await user.click(within(dialog).getByRole("button", { name: "Save revised changes" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Custom printer model" }),
      ).not.toBeInTheDocument(),
    );
    expect(changes[1]).toEqual({ model_name: "Homebrew CoreXY" });
    expect(patches).toBe(2);
    expect(headers[0]?.get("If-Match")).toBe(`"printer-1-e${original.edit_epoch}-v1"`);
    expect(headers[1]?.get("If-Match")).toBe(`"printer-1-e${original.edit_epoch}-v2"`);
  });

  it("falls back to a custom text field for a model not in the list", async () => {
    renderPrintersPage({ printers: [makePrinter()] });

    await chooseCardAction("Voron 2.4", "Set model");
    await userEvent.click(screen.getByRole("button", { name: "Use custom model" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Model" }), "Homebrew CoreXY");
    await userEvent.click(screen.getByRole("button", { name: "Use this model" }));

    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    const patched = requestsWithMethod("PATCH")[0]!;
    expect(patched.url).toBe("/api/v1/printers/1");
    const payload: PrinterUpdate = JSON.parse(patched.body);
    expect(payload).toEqual({ model_name: "Homebrew CoreXY" });
  });
});

describe("PrintersPage", () => {
  describe("removing a printer", () => {
    async function selectAction(name: string, action: "Edit connection" | "Remove printer") {
      await chooseCardAction(name, action);
    }

    beforeAll(async () => {
      // Resolve the editor's lazy module before querying its controls.
      await import("@/components/printer-detail");
    });

    it("asks before removing", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      await selectAction("Voron 2.4", "Remove printer");

      expect(screen.getByRole("dialog", { name: "Remove printer?" })).toBeInTheDocument();
      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("keeps removal available from connection settings", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      await selectAction("Voron 2.4", "Edit connection");
      const settings = screen.getByRole("dialog", { name: "Edit printer connection" });
      await userEvent.click(
        await within(settings).findByRole("button", { name: "Remove printer" }),
      );

      expect(
        await screen.findByText('"Voron 2.4" will be removed from PrintStash.'),
      ).toBeInTheDocument();
    });

    it("keeps the printer when removal is canceled", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      await selectAction("Voron 2.4", "Remove printer");
      const dialog = screen.getByRole("dialog", { name: "Remove printer?" });
      await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

      expect(screen.getByRole("link", { name: "Voron 2.4" })).toBeVisible();
      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("removes the selected printer once confirmed", async () => {
      renderPrintersPage({
        printers: [makePrinter(), makePrinter({ id: 2, name: "Lab Klipper" })],
      });
      await selectAction("Lab Klipper", "Remove printer");
      const dialog = await screen.findByRole("dialog", { name: "Remove printer?" });
      expect(dialog).toHaveAccessibleDescription('"Lab Klipper" will be removed from PrintStash.');
      await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));

      await waitFor(() => expect(requestsWithMethod("DELETE")).toHaveLength(1));
      expect(requestsWithMethod("DELETE")[0]!.url).toBe("/api/v1/printers/2");
    });

    it("offers no removal to somebody who may not administer it", async () => {
      // The connection is visible, while administrative actions remain unavailable.
      renderPrintersPage({
        auth: MEMBER_AUTH,
        printers: [
          makePrinter({
            access: {
              role: "print",
              can_view: true,
              can_print: true,
              can_control: false,
              can_admin: false,
            },
          }),
        ],
      });

      openCardMenu("Voron 2.4");

      expect(screen.getByRole("menuitem", { name: "Open page" })).toBeVisible();
      expect(screen.queryByRole("menuitem", { name: "Remove printer" })).not.toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "Edit connection" })).not.toBeInTheDocument();
      expect(requestsWithMethod("DELETE")).toEqual([]);
    });

    it("offers administration only on printers the member can administer", async () => {
      const managed = makePrinter();
      const viewOnly = makePrinter({
        id: 2,
        name: "View-only printer",
        access: {
          role: "view",
          can_view: true,
          can_print: false,
          can_control: false,
          can_admin: false,
        },
      });
      renderPrintersPage({ auth: MEMBER_AUTH, printers: [managed, viewOnly] });

      openCardMenu(viewOnly.name);
      const menu = await screen.findByRole("menu");
      expect(within(menu).getByRole("menuitem", { name: "Open page" })).toHaveAttribute(
        "href",
        "/printers/2",
      );
      expect(
        within(menu).queryByRole("menuitem", { name: "Remove printer" }),
      ).not.toBeInTheDocument();
    });

    it("shows the empty state before the first printer is added", async () => {
      renderPrintersPage();

      expect(screen.getByText("No printers configured yet.")).toBeVisible();
    });
  });

  describe("the fleet tabs", () => {
    it("offers the queue to somebody who may print", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      expect(screen.getByRole("tab", { name: "Queue" })).toBeInTheDocument();
    });

    it("offers maintenance to somebody who may administer a printer", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      expect(screen.getByRole("tab", { name: "Maintenance" })).toBeInTheDocument();
    });

    it("has no separate management tab; actions live in the card menu", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      expect(screen.queryByRole("tab", { name: "Manage printers" })).not.toBeInTheDocument();
      openCardMenu("Voron 2.4");
      expect(screen.getByRole("menuitem", { name: "Remove printer" })).toBeVisible();
    });

    it("opens the queue when it is chosen", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      await userEvent.click(screen.getByRole("tab", { name: "Queue" }));

      expect(screen.getByRole("tab", { name: "Queue" })).toHaveAttribute("aria-selected", "true");
    });
  });
});
