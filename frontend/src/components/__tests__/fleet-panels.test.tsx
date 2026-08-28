/*
 * The two panels an operator actually drives a print farm from.
 *
 * Everything here is about the *call* the UI makes, because the state lives on the
 * server. Reordering is the sharp case: the queue is ordered by an integer
 * position, so moving a job up has to send the new position rather than a
 * direction — a UI that sent "up" and let the server guess would reorder
 * differently than the list it just animated. Both directions are asserted,
 * since an off-by-one is symmetric and looks right from one end.
 *
 * Deleting confirms first; nothing else does. That asymmetry is deliberate and it
 * is the only irreversible action in the panel.
 *
 * Drain is asserted in both directions too. Toggling it on is what an operator
 * does before servicing a machine; failing to send `false` on resume leaves a
 * printer permanently out of rotation with the UI showing it as available.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  FleetMaintenancePanel,
  FleetQueuePanel,
  type FleetMaintenanceDeps,
  type FleetQueueDeps,
} from "@/components/fleet-panels";
import { defaultQueryApi, QueryApiProvider, type QueryApi } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import type {
  FleetSummary,
  MaintenanceLog,
  MaintenanceWindow,
  PrinterRead,
  PrintJobRead,
} from "@/types";

// Both panels take their fleet mutations through an optional `deps` prop, so
// those are stubbed by passing them in. The queue panel's reads are the real
// `useFleetQueue`/`useFleetSummary` hooks, driven by the pre-seeded
// `QueryClient` cache `renderQueuePanel` renders them under.
// Toasts are left as the real thing — sonner is happy without a mounted
// `<Toaster />` and nothing here asserts on them.
const deleteJob = vi.fn<FleetQueueDeps["deleteJob"]>();
const updateJob = vi.fn<FleetQueueDeps["updateJob"]>();
const retryJob = vi.fn<FleetQueueDeps["retryJob"]>();
const decideOperatorGate = vi.fn<FleetQueueDeps["decideOperatorGate"]>();

const queueDeps: FleetQueueDeps = {
  deleteJob,
  updateJob,
  retryJob,
  decideOperatorGate,
};

const listWindows = vi.fn<FleetMaintenanceDeps["listWindows"]>();
const listLog = vi.fn<FleetMaintenanceDeps["listLog"]>();
const createWindow = vi.fn<FleetMaintenanceDeps["createWindow"]>();
const createLog = vi.fn<FleetMaintenanceDeps["createLog"]>();
const deleteWindow = vi.fn<FleetMaintenanceDeps["deleteWindow"]>();
const deleteLog = vi.fn<FleetMaintenanceDeps["deleteLog"]>();
const updateRouting = vi.fn<FleetMaintenanceDeps["updateRouting"]>();

const maintenanceDeps: FleetMaintenanceDeps = {
  listWindows,
  listLog,
  createWindow,
  createLog,
  deleteWindow,
  deleteLog,
  updateRouting,
};

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
    external_display_name: null,
    external_task_id: null,
    external_subtask_id: null,
    external_project_id: null,
    external_profile_id: null,
    external_gcode_file: null,
    external_plate_index: null,
    external_current_layer: null,
    external_total_layers: null,
    external_nozzle_diameter: null,
    artifact_evidence: "vault",
    artifact_capture_error: null,
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

function makeSummary(overrides: Partial<FleetSummary> = {}): FleetSummary {
  return {
    total_printers: 0,
    queued_jobs: 0,
    active_jobs: 0,
    draining_printers: 0,
    maintenance_printers: 0,
    attention_jobs: 0,
    ...overrides,
  };
}

function makeWindow(overrides: Partial<MaintenanceWindow> = {}): MaintenanceWindow {
  return {
    id: 1,
    printer_id: 1,
    starts_at: "2026-08-01T09:00:00Z",
    ends_at: "2026-08-01T11:00:00Z",
    reason: "Nozzle swap",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

function makeLog(overrides: Partial<MaintenanceLog> = {}): MaintenanceLog {
  return {
    id: 1,
    printer_id: 1,
    performed_at: "2026-07-15T00:00:00Z",
    category: "belt",
    note: "Tensioned X belt",
    counter_value: null,
    counter_unit: null,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

/** FleetQueuePanel's history window is part of the fleet-queue query key. */
const FLEET_QUEUE_HISTORY_LIMIT = 20;

/**
 * Renders the queue panel over its real query hooks: the cache is seeded with
 * the given jobs and summary and held stale-free, so the first render already
 * has data and nothing falls back to the network. `QueryApiProvider` keeps the
 * `refetch()` the panel fires after a mutation resolving to the same data.
 */
function renderQueuePanel(
  seed: { printers?: PrinterRead[]; jobs?: PrintJobRead[]; summary?: FleetSummary } = {},
) {
  const jobs = seed.jobs ?? [];
  const summary = seed.summary ?? makeSummary();
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
    },
  });
  client.setQueryData([...queryKeys.fleetQueue, FLEET_QUEUE_HISTORY_LIMIT], jobs);
  client.setQueryData(queryKeys.fleetSummary, summary);
  const api: QueryApi = {
    ...defaultQueryApi,
    listFleetQueue: () => Promise.resolve(jobs),
    getFleetSummary: () => Promise.resolve(summary),
  };

  return render(
    <QueryApiProvider value={api}>
      <QueryClientProvider client={client}>
        <FleetQueuePanel printers={seed.printers ?? [makePrinter()]} deps={queueDeps} />
      </QueryClientProvider>
    </QueryApiProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listWindows.mockResolvedValue([]);
  listLog.mockResolvedValue([]);
});

describe("FleetQueuePanel", () => {
  it("renders queued, active, and recent jobs grouped into sections", () => {
    renderQueuePanel({
      jobs: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
        makeJob({ id: 3, state: "printing", remote_filename: "active.gcode" }),
        makeJob({ id: 4, state: "completed", remote_filename: "done.gcode" }),
      ],
    });

    expect(screen.getByRole("heading", { name: "Queued" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Active" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent" })).toBeInTheDocument();
    expect(screen.getByText("first.gcode")).toBeInTheDocument();
    expect(screen.getByText("active.gcode")).toBeInTheDocument();
    expect(screen.getByText("done.gcode")).toBeInTheDocument();
  });

  it("shows the empty state when there are no jobs", () => {
    renderQueuePanel({ printers: [] });
    expect(screen.getByText("No queued print jobs")).toBeInTheDocument();
  });

  it("moving a queued job down calls updateFleetJob with the new queue position", async () => {
    updateJob.mockResolvedValue(makeJob());
    renderQueuePanel({
      jobs: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Move first.gcode down" }));

    await waitFor(() => expect(updateJob).toHaveBeenCalledWith(1, { queue_position: 2 }));
  });

  it("moving a queued job up calls updateFleetJob with the new queue position", async () => {
    updateJob.mockResolvedValue(makeJob());
    renderQueuePanel({
      jobs: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Move second.gcode up" }));

    await waitFor(() => expect(updateJob).toHaveBeenCalledWith(2, { queue_position: 1 }));
  });

  it("deleting a queued job confirms then calls deleteFleetJob", async () => {
    deleteJob.mockResolvedValue(undefined);
    renderQueuePanel({
      jobs: [
        makeJob({ id: 5, state: "queued", queue_position: 1, remote_filename: "cancel-me.gcode" }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Delete cancel-me.gcode" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete job" }));

    await waitFor(() => expect(deleteJob).toHaveBeenCalledWith(5));
  });

  it("edits routing, priority, and position for a queued job", async () => {
    updateJob.mockResolvedValue(makeJob());
    renderQueuePanel({
      jobs: [
        makeJob({
          id: 7,
          remote_filename: "edit-me.gcode",
          updated_at: "2026-07-15T00:00:00Z",
        }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Edit edit-me.gcode" }));
    const dialog = screen.getByRole("dialog", { name: "Edit queue job" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Routing"), "manual");
    await userEvent.selectOptions(within(dialog).getByLabelText("Printer"), "1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Priority"), "rush");
    await userEvent.clear(within(dialog).getByLabelText("Queue position"));
    await userEvent.type(within(dialog).getByLabelText("Queue position"), "3");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateJob).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          strategy: "manual",
          printer_id: 1,
          priority: "rush",
          queue_position: 3,
          expected_updated_at: "2026-07-15T00:00:00Z",
        }),
      ),
    );
  });

  it("retrying a failed retryable job calls retryFleetJob", async () => {
    retryJob.mockResolvedValue(makeJob({ id: 9, state: "queued" }));
    renderQueuePanel({
      jobs: [
        makeJob({ id: 9, state: "failed", retryable: true, remote_filename: "retry-me.gcode" }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(retryJob).toHaveBeenCalledWith(9));
  });
});

describe("FleetMaintenancePanel", () => {
  it("shows the empty state with no printers", () => {
    render(
      <FleetMaintenancePanel
        printers={[]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );
    expect(screen.getByText("No printers to maintain")).toBeInTheDocument();
  });

  it("toggling soft drain calls updatePrinterRouting with drain_mode true", async () => {
    updateRouting.mockResolvedValue({});
    const onPrintersChanged = vi.fn<() => void>();
    render(
      <FleetMaintenancePanel
        printers={[makePrinter({ drain_mode: false })]}
        onPrintersChanged={onPrintersChanged}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Soft drain" }));

    await waitFor(() =>
      expect(updateRouting).toHaveBeenCalledWith(1, {
        drain_mode: true,
        drain_reason: "Manual soft drain",
      }),
    );
    expect(onPrintersChanged).toHaveBeenCalled();
  });

  it("resuming a drained printer calls updatePrinterRouting with drain_mode false", async () => {
    updateRouting.mockResolvedValue({});
    render(
      <FleetMaintenancePanel
        printers={[makePrinter({ drain_mode: true, drain_reason: "Nozzle swap" })]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Resume routing" }));

    await waitFor(() =>
      expect(updateRouting).toHaveBeenCalledWith(1, {
        drain_mode: false,
        drain_reason: null,
      }),
    );
  });

  it("scheduling a maintenance window calls createMaintenanceWindow with the entered fields", async () => {
    createWindow.mockResolvedValue(makeWindow());
    render(
      <FleetMaintenancePanel
        printers={[makePrinter()]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    const dialog = screen.getByRole("dialog", { name: "Schedule maintenance" });
    await userEvent.type(within(dialog).getByLabelText("Starts"), "2026-08-01T09:00");
    await userEvent.type(within(dialog).getByLabelText("Ends"), "2026-08-01T11:00");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Nozzle swap");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createWindow).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ reason: "Nozzle swap" }),
      ),
    );
  });

  it("logging maintenance calls createMaintenanceLog with the category and note", async () => {
    createLog.mockResolvedValue(makeLog());
    render(
      <FleetMaintenancePanel
        printers={[makePrinter()]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    const dialog = screen.getByRole("dialog", { name: "Log maintenance" });
    await userEvent.clear(within(dialog).getByLabelText("Category"));
    await userEvent.type(within(dialog).getByLabelText("Category"), "belt");
    await userEvent.type(within(dialog).getByLabelText("Note"), "Tensioned X belt");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createLog).toHaveBeenCalledWith(1, {
        category: "belt",
        note: "Tensioned X belt",
      }),
    );
  });
});
