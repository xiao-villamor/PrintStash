import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FleetMaintenancePanel, FleetQueuePanel } from "@/components/fleet-panels";
import type { MaintenanceLog, MaintenanceWindow, PrinterRead, PrintJobRead } from "@/types";

const {
  cancelFleetJob,
  updateFleetJob,
  retryFleetJob,
  updatePrinterRouting,
  createMaintenanceWindow,
  createMaintenanceLog,
  listMaintenanceWindows,
  listMaintenanceLog,
  deleteMaintenanceWindow,
  deleteMaintenanceLog,
} = vi.hoisted(() => ({
  cancelFleetJob: vi.fn(),
  updateFleetJob: vi.fn(),
  retryFleetJob: vi.fn(),
  updatePrinterRouting: vi.fn(),
  createMaintenanceWindow: vi.fn(),
  createMaintenanceLog: vi.fn(),
  listMaintenanceWindows: vi.fn(),
  listMaintenanceLog: vi.fn(),
  deleteMaintenanceWindow: vi.fn(),
  deleteMaintenanceLog: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  cancelFleetJob,
  updateFleetJob,
  retryFleetJob,
  updatePrinterRouting,
  createMaintenanceWindow,
  createMaintenanceLog,
  listMaintenanceWindows,
  listMaintenanceLog,
  deleteMaintenanceWindow,
  deleteMaintenanceLog,
}));

const mockUseFleetQueue = vi.fn<() => { data: PrintJobRead[]; isLoading: boolean; refetch: () => void }>();
const mockUseFleetSummary = vi.fn<() => { data: unknown; refetch: () => void }>();
vi.mock("@/lib/queries", () => ({
  useFleetQueue: () => mockUseFleetQueue(),
  useFleetSummary: () => mockUseFleetSummary(),
}));

vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

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

function makeJob(overrides: Partial<PrintJobRead> = {}): PrintJobRead {
  return {
    id: 1,
    printer_id: 1,
    file_id: 10,
    model_id: 1,
    remote_filename: "bracket.gcode",
    state: "queued",
    progress: 0,
    source: "vault",
    error: null,
    routing_strategy: "least_busy",
    queue_position: 1,
    provider_job_id: null,
    blocked_reason: null,
    dispatch_claimed_at: null,
    dispatch_attempts: 0,
    retryable: false,
    requested_by: null,
    spool_id: null,
    spool_name: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseFleetQueue.mockReturnValue({ data: [], isLoading: false, refetch: vi.fn() });
  mockUseFleetSummary.mockReturnValue({
    data: { queued_jobs: 0, active_jobs: 0, attention_jobs: 0, draining_printers: 0 },
    refetch: vi.fn(),
  });
  listMaintenanceWindows.mockResolvedValue([]);
  listMaintenanceLog.mockResolvedValue([]);
});

describe("FleetQueuePanel", () => {
  it("renders queued, active, and recent jobs grouped into sections", () => {
    mockUseFleetQueue.mockReturnValue({
      data: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
        makeJob({ id: 3, state: "printing", remote_filename: "active.gcode" }),
        makeJob({ id: 4, state: "completed", remote_filename: "done.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} />);

    expect(screen.getByRole("heading", { name: "Queued" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Active" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent" })).toBeInTheDocument();
    expect(screen.getByText("first.gcode")).toBeInTheDocument();
    expect(screen.getByText("active.gcode")).toBeInTheDocument();
    expect(screen.getByText("done.gcode")).toBeInTheDocument();
  });

  it("shows the empty state when there are no jobs", () => {
    render(<FleetQueuePanel printers={[]} />);
    expect(screen.getByText("No queued print jobs")).toBeInTheDocument();
  });

  it("moving a queued job down calls updateFleetJob with the new queue position", async () => {
    updateFleetJob.mockResolvedValue({});
    mockUseFleetQueue.mockReturnValue({
      data: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} />);

    await userEvent.click(screen.getByRole("button", { name: "Move first.gcode down" }));

    await waitFor(() => expect(updateFleetJob).toHaveBeenCalledWith(1, { queue_position: 2 }));
  });

  it("moving a queued job up calls updateFleetJob with the new queue position", async () => {
    updateFleetJob.mockResolvedValue({});
    mockUseFleetQueue.mockReturnValue({
      data: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} />);

    await userEvent.click(screen.getByRole("button", { name: "Move second.gcode up" }));

    await waitFor(() => expect(updateFleetJob).toHaveBeenCalledWith(2, { queue_position: 1 }));
  });

  it("cancelling a queued job confirms then calls cancelFleetJob", async () => {
    cancelFleetJob.mockResolvedValue(undefined);
    mockUseFleetQueue.mockReturnValue({
      data: [makeJob({ id: 5, state: "queued", queue_position: 1, remote_filename: "cancel-me.gcode" })],
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} />);

    await userEvent.click(screen.getByRole("button", { name: "Cancel cancel-me.gcode" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));

    await waitFor(() => expect(cancelFleetJob).toHaveBeenCalledWith(5));
  });

  it("retrying a failed retryable job calls retryFleetJob", async () => {
    retryFleetJob.mockResolvedValue({});
    mockUseFleetQueue.mockReturnValue({
      data: [makeJob({ id: 9, state: "failed", retryable: true, remote_filename: "retry-me.gcode" })],
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} />);

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(retryFleetJob).toHaveBeenCalledWith(9));
  });
});

describe("FleetMaintenancePanel", () => {
  it("shows the empty state with no printers", () => {
    render(<FleetMaintenancePanel printers={[]} onPrintersChanged={vi.fn()} />);
    expect(screen.getByText("No printers to maintain")).toBeInTheDocument();
  });

  it("toggling soft drain calls updatePrinterRouting with drain_mode true", async () => {
    updatePrinterRouting.mockResolvedValue({});
    const onPrintersChanged = vi.fn();
    render(
      <FleetMaintenancePanel printers={[makePrinter({ drain_mode: false })]} onPrintersChanged={onPrintersChanged} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Soft drain" }));

    await waitFor(() =>
      expect(updatePrinterRouting).toHaveBeenCalledWith(1, {
        drain_mode: true,
        drain_reason: "Manual soft drain",
      }),
    );
    expect(onPrintersChanged).toHaveBeenCalled();
  });

  it("resuming a drained printer calls updatePrinterRouting with drain_mode false", async () => {
    updatePrinterRouting.mockResolvedValue({});
    render(
      <FleetMaintenancePanel
        printers={[makePrinter({ drain_mode: true, drain_reason: "Nozzle swap" })]}
        onPrintersChanged={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Resume routing" }));

    await waitFor(() =>
      expect(updatePrinterRouting).toHaveBeenCalledWith(1, { drain_mode: false, drain_reason: null }),
    );
  });

  it("scheduling a maintenance window calls createMaintenanceWindow with the entered fields", async () => {
    createMaintenanceWindow.mockResolvedValue({} as MaintenanceWindow);
    render(<FleetMaintenancePanel printers={[makePrinter()]} onPrintersChanged={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    const dialog = screen.getByRole("dialog", { name: "Schedule maintenance" });
    await userEvent.type(within(dialog).getByLabelText("Starts"), "2026-08-01T09:00");
    await userEvent.type(within(dialog).getByLabelText("Ends"), "2026-08-01T11:00");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Nozzle swap");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createMaintenanceWindow).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ reason: "Nozzle swap" }),
      ),
    );
  });

  it("logging maintenance calls createMaintenanceLog with the category and note", async () => {
    createMaintenanceLog.mockResolvedValue({} as MaintenanceLog);
    render(<FleetMaintenancePanel printers={[makePrinter()]} onPrintersChanged={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    const dialog = screen.getByRole("dialog", { name: "Log maintenance" });
    await userEvent.clear(within(dialog).getByLabelText("Category"));
    await userEvent.type(within(dialog).getByLabelText("Category"), "belt");
    await userEvent.type(within(dialog).getByLabelText("Note"), "Tensioned X belt");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createMaintenanceLog).toHaveBeenCalledWith(1, { category: "belt", note: "Tensioned X belt" }),
    );
  });
});
