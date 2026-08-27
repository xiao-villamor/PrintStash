import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

import { SendToButtons, type SendToCommands } from "@/components/model-detail/send-to-buttons";
import { storeLogin } from "@/lib/auth";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { queryKeys } from "@/lib/query-client";
import type {
  MetadataRead,
  PrintBatchRead,
  PrinterRead,
  PrintJobRead,
  SpoolmanStatus,
  SpoolRead,
} from "@/types";

// The panel's four fleet commands are injected, so the test observes the exact
// payload the UI submits without a network in the way.
const checkFleetCompatibility = vi.fn<SendToCommands["checkFleetCompatibility"]>();
const createFleetBatch = vi.fn<SendToCommands["createFleetBatch"]>();
const enqueueFleetJob = vi.fn<SendToCommands["enqueueFleetJob"]>();
const sendToPrinter = vi.fn<SendToCommands["sendToPrinter"]>();
const commands: SendToCommands = {
  checkFleetCompatibility,
  createFleetBatch,
  enqueueFleetJob,
  sendToPrinter,
};

const printer: PrinterRead = {
  id: 7,
  name: "Farm printer",
  provider: "moonraker",
  moonraker_url: "http://farm",
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
  created_at: "2026-07-15T00:00:00Z",
  updated_at: "2026-07-15T00:00:00Z",
};

const adminUser = { id: 1, username: "admin", email: null, is_superuser: true };

// The panel reads the session through the real context; nothing in these cases
// signs in or out, so the auth commands are inert.
const adminAuth: AuthState = {
  user: adminUser,
  loading: false,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
};

const queuedJob: PrintJobRead = {
  id: 1,
  printer_id: 7,
  file_id: 42,
  model_id: 1,
  remote_filename: "cube.gcode",
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
};

const queuedBatch: PrintBatchRead = {
  id: 1,
  file_id: 42,
  model_id: 1,
  quantity: 1,
  routing_strategy: "least_busy",
  priority: "normal",
  target_group: null,
  compatibility_policy: "safe",
  requested_by: null,
  created_at: "2026-07-15T00:00:00Z",
  updated_at: "2026-07-15T00:00:00Z",
  jobs: [],
};

const NO_SLICER_METADATA: MetadataRead = {
  slicer_name: null,
  slicer_version: null,
  printer_model: null,
  nozzle_diameter_mm: null,
  layer_height_mm: null,
  first_layer_height_mm: null,
  infill_percent: null,
  wall_loops: null,
  top_shell_layers: null,
  bottom_shell_layers: null,
  support_material: null,
  nozzle_temperature_c: null,
  bed_temperature_c: null,
  estimated_time_s: null,
  filament_weight_g: null,
  filament_length_mm: null,
  filament_cost: null,
  material_type: null,
  material_brand: null,
  bbox_x_mm: null,
  bbox_y_mm: null,
  bbox_z_mm: null,
  volume_mm3: null,
  triangle_count: null,
};

/** Slicer metadata whose only interesting fact is the filament estimate. */
function weighing(grams: number): MetadataRead {
  return { ...NO_SLICER_METADATA, filament_weight_g: grams };
}

function spoolmanStatus(enabled: boolean): SpoolmanStatus {
  return {
    enabled,
    base_url: null,
    has_api_key: false,
    write_enabled: false,
    write_force: false,
    connected: enabled,
    version: null,
    error: null,
    native_hook_detected: false,
  };
}

interface PanelOptions {
  /** Slicer metadata on the single G-code revision the panel offers. */
  metadata?: MetadataRead | null;
  spools?: SpoolRead[];
}

// The shared reads (printers, Spoolman) go through the real query hooks against
// a cache seeded with fresh data, so nothing refetches and the panel sees the
// same shapes the API returns.
function renderPanel({ metadata = null, spools }: PanelOptions = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
  client.setQueryData(queryKeys.printers, [printer]);
  client.setQueryData(queryKeys.spoolmanStatus, spoolmanStatus(spools !== undefined));
  client.setQueryData(queryKeys.spools, spools ?? []);

  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={adminAuth}>
          <SendToButtons
            commands={commands}
            gcodeFiles={[
              {
                id: 42,
                original_filename: "cube.gcode",
                version: 1,
                gcode_revision_number: 1,
                revision_label: null,
                is_recommended: true,
                metadata,
              },
            ]}
            printerFiles={[]}
          />
        </AuthContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // `useRequireAuth` gates the send buttons on a stored session.
  storeLogin("", adminUser, { silent: true });
  enqueueFleetJob.mockReset();
  enqueueFleetJob.mockResolvedValue(queuedJob);
  createFleetBatch.mockReset();
  createFleetBatch.mockResolvedValue(queuedBatch);
  checkFleetCompatibility.mockReset();
  checkFleetCompatibility.mockResolvedValue({
    file_id: 42,
    requirements: [],
    nozzle_diameter_mm: null,
    printers: [],
  });
});

it("adds selected G-code to least-busy fleet queue", async () => {
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await userEvent.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);

  await waitFor(() =>
    expect(enqueueFleetJob).toHaveBeenCalledWith(
      expect.objectContaining({
        file_id: 42,
        strategy: "least_busy",
        printer_id: undefined,
      }),
    ),
  );
});

it("warns when the selected spool doesn't have enough filament left, but doesn't block sending", async () => {
  renderPanel({
    metadata: weighing(250),
    spools: [
      {
        id: 1,
        filament_id: null,
        name: "Almost empty",
        filament_name: null,
        vendor_name: null,
        material: null,
        color_hex: null,
        remaining_weight: 10,
        used_weight: null,
        archived: false,
        location: null,
      },
    ],
  });

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.selectOptions(screen.getByLabelText("Spool"), "1");

  expect(await screen.findByText(/needs ~250g.*10g left/)).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)).not.toBeDisabled();
});

it("warns when the spool has no tracked remaining weight instead of assuming it's plenty", async () => {
  renderPanel({
    metadata: weighing(250),
    spools: [
      {
        id: 1,
        filament_id: null,
        name: "Untracked",
        filament_name: null,
        vendor_name: null,
        material: null,
        color_hex: null,
        remaining_weight: null,
        used_weight: null,
        archived: false,
        location: null,
      },
    ],
  });

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.selectOptions(screen.getByLabelText("Spool"), "1");

  expect(await screen.findByText(/no tracked remaining weight/)).toBeInTheDocument();
});

it("confirms a known manual mismatch and records the override policy", async () => {
  checkFleetCompatibility.mockResolvedValue({
    file_id: 42,
    requirements: [{ tool_index: 0, material_type: "PLA", color_hex: null }],
    nozzle_diameter_mm: 0.4,
    printers: [
      {
        printer_id: 7,
        verdict: "mismatch",
        reasons: ["material_type_mismatch"],
        missing_materials: ["pla"],
        color_advisories: [],
      },
    ],
  });
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await userEvent.selectOptions(screen.getByLabelText("Routing"), "manual");
  await userEvent.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);
  expect(await screen.findByText("Print with a known material mismatch?")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Print anyway" }));

  await waitFor(() =>
    expect(enqueueFleetJob).toHaveBeenCalledWith(
      expect.objectContaining({
        compatibility_policy: "allow_mismatch",
        printer_id: 7,
      }),
    ),
  );
});

it("creates an atomic batch when copies is greater than one", async () => {
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await userEvent.clear(screen.getByLabelText("Copies"));
  await userEvent.type(screen.getByLabelText("Copies"), "3");
  await userEvent.selectOptions(screen.getByLabelText("Priority"), "rush");
  await userEvent.type(screen.getByLabelText("Printer group"), "Workshop");
  await userEvent.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);

  await waitFor(() =>
    expect(createFleetBatch).toHaveBeenCalledWith(
      expect.objectContaining({
        file_id: 42,
        quantity: 3,
        priority: "rush",
        target_group: "Workshop",
      }),
    ),
  );
});
