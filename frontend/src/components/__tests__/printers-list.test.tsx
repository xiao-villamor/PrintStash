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

import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { PrintersPage } from "@/components/printers-list";
import { writePrinterCardImagePreference } from "@/lib/printer-card-display";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { invalidateApiCache } from "@/lib/api/request";
import { queryKeys } from "@/lib/query-client";
import type { Dashboard, FleetSummary, PrinterCreate, PrinterRead, PrinterUpdate } from "@/types";

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

const EMPTY_DASHBOARD: Dashboard = {
  total_printers: 0,
  status_counts: {},
  active_jobs: 0,
  groups: [],
};

const EMPTY_FLEET_SUMMARY: FleetSummary = {
  total_printers: 0,
  queued_jobs: 0,
  active_jobs: 0,
  draining_printers: 0,
  maintenance_printers: 0,
  attention_jobs: 0,
};

const ADMIN_AUTH: AuthState = {
  user: { id: 1, username: "admin", email: null, is_superuser: true },
  loading: false,
  login: vi.fn<AuthState["login"]>(),
  logout: vi.fn<AuthState["logout"]>(),
  refresh: vi.fn<AuthState["refresh"]>(),
};

/** FleetQueuePanel's history window is part of its query key. */
const FLEET_QUEUE_HISTORY_LIMIT = 20;

function renderPrintersPage(seed: { printers?: PrinterRead[]; dashboard?: Dashboard } = {}) {
  const client = new QueryClient({
    defaultOptions: {
      // Seeded data must stay put: nothing here may fall back to the network.
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
    },
  });
  client.setQueryData(queryKeys.printers, seed.printers ?? []);
  client.setQueryData(queryKeys.printerDashboard, seed.dashboard ?? EMPTY_DASHBOARD);
  client.setQueryData([...queryKeys.fleetQueue, FLEET_QUEUE_HISTORY_LIMIT], []);
  client.setQueryData(queryKeys.fleetSummary, EMPTY_FLEET_SUMMARY);

  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={ADMIN_AUTH}>
          <PrintersPage />
        </AuthContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function makePrinter(overrides: Partial<PrinterRead> = {}): PrinterRead {
  return {
    id: 1,
    name: "Voron 2.4",
    provider: "moonraker",
    moonraker_url: "http://10.0.0.1:7125",
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
  fetchMock.mockImplementation(() => Promise.resolve(printerResponse(makePrinter())));
  invalidateApiCache();
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
  it("submits only once when add is triggered twice before request resolves", async () => {
    let resolveCreate!: () => void;
    fetchMock.mockImplementation((_input, init) =>
      init?.method === "POST"
        ? new Promise<Response>((resolve) => {
            resolveCreate = () => resolve(printerResponse(makePrinter()));
          })
        : Promise.resolve(printerResponse(makePrinter())),
    );
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Voron");
    await userEvent.type(screen.getByLabelText("Moonraker URL"), "http://voron.local:7125");
    const form = screen
      .getAllByRole("button", { name: /^add printer$/i })
      .at(-1)!
      .closest("form")!;

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
    expect(screen.getByText("No queued print jobs")).toBeInTheDocument();
  });

  it("summarizes fleet health and filters by printer group", async () => {
    renderPrintersPage({
      printers: [
        makePrinter({ id: 1, name: "Workshop Voron", group: "Workshop", status: "printing" }),
        makePrinter({ id: 2, name: "Garage Prusa", group: "Garage", status: "offline" }),
      ],
      dashboard: {
        total_printers: 2,
        status_counts: { printing: 1, offline: 1 },
        active_jobs: 1,
        groups: [
          { name: "Garage", count: 1, status_counts: { offline: 1 } },
          { name: "Workshop", count: 1, status_counts: { printing: 1 } },
        ],
      },
    });

    expect(screen.getByLabelText("Fleet summary")).toHaveTextContent("1");
    // DOM implementations differ on whether adjacent text nodes insert a space.
    await userEvent.click(screen.getByRole("button", { name: /Workshop\s*1/ }));
    expect(screen.getByRole("link", { name: "Workshop Voron" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Garage Prusa" })).not.toBeInTheDocument();
  });

  it("shows optional printer artwork only when enabled in display settings", () => {
    const plain = renderPrintersPage({ printers: [makePrinter()] });
    expect(screen.queryByAltText("Voron 2.4 printer")).not.toBeInTheDocument();
    plain.unmount();

    window.localStorage.setItem("printstash.printer-card.show-image", "true");
    renderPrintersPage({ printers: [makePrinter()] });
    expect(screen.getByAltText("Voron 2.4 printer")).toBeInTheDocument();
  });

  it("updates a mounted printer page when the saved display preference changes", () => {
    renderPrintersPage({ printers: [makePrinter()] });
    expect(screen.queryByAltText("Voron 2.4 printer")).not.toBeInTheDocument();

    act(() => writePrinterCardImagePreference(true));

    expect(screen.getByAltText("Voron 2.4 printer")).toBeInTheDocument();
  });

  it("shows the detected model", () => {
    renderPrintersPage({ printers: [makePrinter({ detected_model: "Bambu Lab X1 Carbon" })] });

    expect(screen.getByText("Bambu Lab X1 Carbon")).toBeInTheDocument();
  });

  it("lets the user pick a model from the list when nothing was detected", async () => {
    const user = userEvent.setup();
    renderPrintersPage({ printers: [makePrinter()] });

    await user.click(screen.getByText("Set model"));
    const picker = screen.getByRole("dialog", { name: "Select printer model" });
    await user.click(within(picker).getByText("Voron 2.4"));
    await user.click(within(picker).getByText("Save model"));

    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    const patched = requestsWithMethod("PATCH")[0]!;
    expect(patched.url).toBe("/api/v1/printers/1");
    const payload: PrinterUpdate = JSON.parse(patched.body);
    expect(payload).toEqual({ model_name: "Voron 2.4" });
  });

  it("falls back to a custom text field for a model not in the list", async () => {
    renderPrintersPage({ printers: [makePrinter()] });

    await userEvent.click(screen.getByText("Set model"));
    await userEvent.type(screen.getByPlaceholderText("Enter model name"), "Homebrew CoreXY");
    await userEvent.click(screen.getByRole("button", { name: "Save model" }));

    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    const patched = requestsWithMethod("PATCH")[0]!;
    expect(patched.url).toBe("/api/v1/printers/1");
    const payload: PrinterUpdate = JSON.parse(patched.body);
    expect(payload).toEqual({ model_name: "Homebrew CoreXY" });
  });
});

describe("PrintersPage", () => {
  describe("removing a printer", () => {
    it("asks before removing", async () => {
      // Removing a printer takes its access grants and its job history with it.
      renderPrintersPage({ printers: [makePrinter()] });

      await userEvent.click(screen.getByRole("button", { name: /Remove/ }));

      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("names the printer it is about to remove", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      await userEvent.click(screen.getByRole("button", { name: /Remove/ }));

      expect(
        await screen.findByText('"Voron 2.4" will be removed from PrintStash.'),
      ).toBeInTheDocument();
    });

    it("removes it once confirmed", async () => {
      renderPrintersPage({ printers: [makePrinter()] });
      await userEvent.click(screen.getByRole("button", { name: /Remove/ }));
      const dialog = await screen.findByRole("dialog");

      await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));

      await waitFor(() => expect(requestsWithMethod("DELETE")).toHaveLength(1));
      expect(requestsWithMethod("DELETE")[0]!.url).toBe("/api/v1/printers/1");
    });

    it("offers no removal to somebody who may not administer it", async () => {
      // The control says "Restricted" rather than disappearing, so a viewer can
      // see the action exists and is not theirs.
      renderPrintersPage({
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

      expect(screen.getByRole("button", { name: /Restricted/ })).toBeDisabled();
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

    it("opens the queue when it is chosen", async () => {
      renderPrintersPage({ printers: [makePrinter()] });

      await userEvent.click(screen.getByRole("tab", { name: "Queue" }));

      expect(screen.getByRole("tab", { name: "Queue" })).toHaveAttribute("aria-selected", "true");
    });
  });
});
