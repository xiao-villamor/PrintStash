import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrintersPage } from "@/components/printers-list";
import { writePrinterCardImagePreference } from "@/lib/printer-card-display";

vi.mock("@/lib/api", () => ({
  createPrinter: vi.fn().mockResolvedValue({}),
  deletePrinter: vi.fn(),
  updatePrinter: vi.fn().mockResolvedValue({}),
}));
const mockUsePrinters = vi.fn<
  () => {
    data: PrinterRead[];
    isLoading: boolean;
    error: Error | null;
    refetch: () => void;
  }
>(() => ({
  data: [],
  isLoading: false,
  error: null,
  refetch: vi.fn(),
}));
const mockUsePrinterDashboard = vi.fn<() => { data: Dashboard; refetch: () => void }>(() => ({
  data: { total_printers: 0, status_counts: {}, active_jobs: 0, groups: [] },
  refetch: vi.fn(),
}));
const mockUseFleetQueue = vi.fn(() => ({ data: [], isLoading: false, refetch: vi.fn() }));
const mockUseFleetSummary = vi.fn(() => ({
  data: {
    total_printers: 0,
    queued_jobs: 0,
    active_jobs: 0,
    draining_printers: 0,
    maintenance_printers: 0,
    attention_jobs: 0,
  },
  refetch: vi.fn(),
}));
vi.mock("@/lib/queries", () => ({
  usePrinters: () => mockUsePrinters(),
  usePrinterDashboard: () => mockUsePrinterDashboard(),
  useFleetQueue: () => mockUseFleetQueue(),
  useFleetSummary: () => mockUseFleetSummary(),
}));
vi.mock("@/lib/use-require-auth", () => ({
  useRequireAuth: () => ({ isAuthenticated: true, showAuthRequiredToast: vi.fn() }),
}));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", email: null, is_superuser: true },
    loading: false,
  }),
}));
vi.mock("@/lib/navigation", () => ({
  Link: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props}>{children}</a>
  ),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { createPrinter, updatePrinter } from "@/lib/api";
import type { Dashboard, PrinterRead } from "@/types";

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
  vi.clearAllMocks();
  mockUsePrinters.mockReturnValue({ data: [], isLoading: false, error: null, refetch: vi.fn() });
  mockUsePrinterDashboard.mockReturnValue({ data: { total_printers: 0, status_counts: {}, active_jobs: 0, groups: [] }, refetch: vi.fn() });
  window.localStorage.clear();
});

async function openForm() {
  render(<PrintersPage />);
  await userEvent.click(screen.getByRole("button", { name: /add printer/i }));
}

describe("printer setup", () => {
  it("submits only once when add is triggered twice before request resolves", async () => {
    let resolveCreate!: () => void;
    vi.mocked(createPrinter).mockImplementationOnce(
      () => new Promise((resolve) => { resolveCreate = () => resolve({} as PrinterRead); }),
    );
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Voron");
    await userEvent.type(screen.getByLabelText("Moonraker URL"), "http://voron.local:7125");
    const form = screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!
      .closest("form")!;

    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(createPrinter).toHaveBeenCalledTimes(1);
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

    await waitFor(() => expect(createPrinter).toHaveBeenCalledWith(expect.objectContaining({
      name: "Prusa MK4",
      provider: "prusalink",
      prusalink_url: "http://mk4.local",
      prusalink_auth_mode: "digest",
      prusalink_username: "maker",
      prusalink_password: "secret",
    })));
    expect(createPrinter).toHaveBeenCalledWith(
      expect.not.objectContaining({ moonraker_url: expect.anything() }),
    );
  });

  it("maps Elegoo Neptune 4 setup to Moonraker variant", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Neptune 4 Max");
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "elegoo_neptune4");
    await userEvent.type(screen.getByLabelText("Printer URL"), "http://neptune.local:7125");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(createPrinter).toHaveBeenCalledWith(expect.objectContaining({
      provider: "moonraker",
      provider_variant: "elegoo_neptune4",
      moonraker_url: "http://neptune.local:7125",
    })));
  });

  it("explains that Centauri Carbon commands need the Mainboard ID while idle", async () => {
    await openForm();
    await userEvent.selectOptions(
      screen.getByLabelText("Integration"),
      "elegoo_centauri_carbon",
    );

    expect(screen.getByLabelText(/Mainboard ID/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        "Needed for reliable printer commands while idle, paused, or errored.",
      ),
    ).toBeInTheDocument();
  });

  it("submits Centauri Carbon 2 local MQTT credentials", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "Centauri Carbon 2");
    await userEvent.selectOptions(
      screen.getByLabelText("Integration"),
      "elegoo_centauri_carbon_2",
    );
    expect(screen.getAllByText(/enable lan only/i)).toHaveLength(2);
    await userEvent.type(screen.getByLabelText("Printer host or IP"), "192.168.1.51");
    await userEvent.type(screen.getByLabelText("Printer access code"), "ABC123");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(createPrinter).toHaveBeenCalledWith(expect.objectContaining({
      provider: "elegoo_centauri",
      provider_variant: "elegoo_centauri_carbon_2",
      elegoo_centauri_host: "192.168.1.51",
      elegoo_centauri_access_code: "ABC123",
    })));
  });

  it("submits OctoPrint URL and API key", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText("Name"), "OctoPi");
    await userEvent.selectOptions(screen.getByLabelText("Integration"), "octoprint");
    await userEvent.type(screen.getByLabelText("OctoPrint URL"), "http://octopi.local");
    await userEvent.type(screen.getByLabelText("API key"), "secret-key");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(createPrinter).toHaveBeenCalledWith(expect.objectContaining({
      provider: "octoprint",
      octoprint_url: "http://octopi.local",
      octoprint_api_key: "secret-key",
    })));
  });
});

describe("printer card", () => {
  it("switches to global queue empty state", async () => {
    render(<PrintersPage />);
    await userEvent.click(screen.getByRole("tab", { name: "Queue" }));
    expect(screen.getByText("No queued print jobs")).toBeInTheDocument();
  });

  it("summarizes fleet health and filters by printer group", async () => {
    mockUsePrinters.mockReturnValue({
      data: [
        makePrinter({ id: 1, name: "Workshop Voron", group: "Workshop", status: "printing" }),
        makePrinter({ id: 2, name: "Garage Prusa", group: "Garage", status: "offline" }),
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUsePrinterDashboard.mockReturnValue({
      data: {
        total_printers: 2,
        status_counts: { printing: 1, offline: 1 },
        active_jobs: 1,
        groups: [
          { name: "Garage", count: 1, status_counts: { offline: 1 } },
          { name: "Workshop", count: 1, status_counts: { printing: 1 } },
        ],
      },
      refetch: vi.fn(),
    });

    render(<PrintersPage />);
    expect(screen.getByLabelText("Fleet summary")).toHaveTextContent("1");
    await userEvent.click(screen.getByRole("button", { name: /Workshop1/ }));
    expect(screen.getByRole("link", { name: "Workshop Voron" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Garage Prusa" })).not.toBeInTheDocument();
  });

  it("shows optional printer artwork only when enabled in display settings", () => {
    mockUsePrinters.mockReturnValue({
      data: [makePrinter()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    const plain = render(<PrintersPage />);
    expect(screen.queryByAltText("Voron 2.4 printer")).not.toBeInTheDocument();
    plain.unmount();

    window.localStorage.setItem("printstash.printer-card.show-image", "true");
    render(<PrintersPage />);
    expect(screen.getByAltText("Voron 2.4 printer")).toBeInTheDocument();
  });

  it("updates a mounted printer page when the saved display preference changes", () => {
    mockUsePrinters.mockReturnValue({
      data: [makePrinter()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<PrintersPage />);
    expect(screen.queryByAltText("Voron 2.4 printer")).not.toBeInTheDocument();

    act(() => writePrinterCardImagePreference(true));

    expect(screen.getByAltText("Voron 2.4 printer")).toBeInTheDocument();
  });

  it("shows the detected model", () => {
    mockUsePrinters.mockReturnValueOnce({
      data: [makePrinter({ detected_model: "Bambu Lab X1 Carbon" })],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<PrintersPage />);

    expect(screen.getByText("Bambu Lab X1 Carbon")).toBeInTheDocument();
  });

  it("lets the user pick a model from the list when nothing was detected", async () => {
    mockUsePrinters.mockReturnValueOnce({
      data: [makePrinter()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<PrintersPage />);

    await userEvent.click(screen.getByText("Set model"));
    expect(screen.getByRole("dialog", { name: "Select printer model" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Voron 2.4" }));
    await userEvent.click(screen.getByRole("button", { name: "Save model" }));

    await waitFor(() =>
      expect(updatePrinter).toHaveBeenCalledWith(1, { model_name: "Voron 2.4" }),
    );
  });

  it("falls back to a custom text field for a model not in the list", async () => {
    mockUsePrinters.mockReturnValueOnce({
      data: [makePrinter()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<PrintersPage />);

    await userEvent.click(screen.getByText("Set model"));
    await userEvent.type(
      screen.getByPlaceholderText("Enter model name"),
      "Homebrew CoreXY",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save model" }));

    await waitFor(() =>
      expect(updatePrinter).toHaveBeenCalledWith(1, {
        model_name: "Homebrew CoreXY",
      }),
    );
  });
});
