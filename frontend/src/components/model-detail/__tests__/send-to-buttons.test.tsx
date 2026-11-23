/*
 * Sending a model to a printer, with every warning that stands between the user
 * and a wasted spool.
 *
 * The material check has three answers and this component has to show all three
 * differently. A *proven* mismatch is confirmed explicitly and the override is
 * recorded, because somebody chose to print PETG on a PLA-loaded machine and that
 * decision needs an author. A spool with no tracked remaining weight is
 * *unknown*, not sufficient — assuming it has enough is how a print fails at 90%.
 * And a compatible spool goes through silently.
 *
 * The batch path is separate because it is atomic: asking for four copies must
 * create one batch, not four independent jobs. Four jobs cannot be cancelled as a
 * unit, which is the whole reason a user asked for a batch.
 */

import "@testing-library/jest-dom/vitest";
import type { ComponentProps } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TestRouter } from "@/test-support/router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SendToButtons, type SendToCommands } from "@/components/model-detail/send-to-buttons";
import { clearLogin } from "@/lib/auth-store";
import { storeLogin } from "@/lib/auth";
import { aRevision, aPrinter, aMetadata } from "@/test-support/factories";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { queryKeys } from "@/lib/query-client";
import type {
  MetadataRead,
  PrintBatchRead,
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

const printer = aPrinter({
  id: 7,
  name: "Farm printer",
  moonraker_url: "http://farm",
  last_seen_at: null,
});

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
  plate_index: null,
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
  layer_count: null,
  exposure_time_s: null,
  resin_volume_ml: null,
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
  volume_measurement: {
    state: "legacy_unassessed",
    unit: "mm3",
    method: null,
    value_mm3: null,
    cause: null,
  },
  triangle_count: null,
};

/** Slicer metadata whose only interesting fact is the filament estimate. */
function weighing(grams: number): MetadataRead {
  return { ...NO_SLICER_METADATA, filament_weight_g: grams };
}

function spoolmanStatus(enabled: boolean): SpoolmanStatus {
  return {
    edit_epoch: "a".repeat(32),
    edit_version: 1,
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
  preselectPlate?: number;
  initialDeliveryMode?: "send" | "queue";
  open?: boolean;
  preselectPrinterId?: number;
  /** Slicer metadata on the single G-code revision the panel offers. */
  metadata?: MetadataRead | null;
  spools?: SpoolRead[];
  filename?: string;
  plates?: number[];
  files?: ComponentProps<typeof SendToButtons>["gcodeFiles"];
  printers?: ReturnType<typeof aPrinter>[];
}

// The shared reads (printers, Spoolman) go through the real query hooks against
// a cache seeded with fresh data, so nothing refetches and the panel sees the
// same shapes the API returns.
function renderPanel({
  preselectPlate,
  metadata = null,
  spools,
  filename = "cube.gcode",
  plates,
  files,
  printers = [printer],
  initialDeliveryMode = "send",
  open,
  preselectPrinterId,
}: PanelOptions = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
  if (plates !== undefined) client.setQueryData(["gcode-plates", 42], { plates });
  client.setQueryData(queryKeys.printers, printers);
  client.setQueryData(queryKeys.spoolmanStatus, spoolmanStatus(spools !== undefined));
  client.setQueryData(queryKeys.spools, spools ?? []);

  const rendered = render(
    <TestRouter>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={adminAuth}>
          <SendToButtons
            commands={commands}
            preselectFileId={preselectPlate === undefined ? undefined : 42}
            preselectPlate={preselectPlate}
            initialDeliveryMode={initialDeliveryMode}
            open={open}
            preselectPrinterId={preselectPrinterId}
            compact={open !== undefined}
            gcodeFiles={
              files ?? [
                {
                  id: 42,
                  original_filename: filename,
                  version: 1,
                  gcode_revision_number: 1,
                  revision_label: null,
                  revision_status: "needs_test",
                  is_recommended: true,
                  metadata,
                },
              ]
            }
            printerFiles={[]}
          />
        </AuthContext.Provider>
      </QueryClientProvider>
    </TestRouter>,
  );
  return { ...rendered, client };
}

beforeEach(() => {
  // `useRequireAuth` gates the send buttons on a stored session.
  storeLogin("", adminUser, { silent: true });
  sendToPrinter.mockReset();
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

describe("SendToQueue", () => {
  it("preserves the inspector's explicit printer through confirmation", async () => {
    const chosen = aPrinter({ id: 9, name: "Chosen printer" });
    sendToPrinter.mockResolvedValue(queuedJob);
    renderPanel({ open: true, preselectPrinterId: 9, printers: [printer, chosen] });
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Select Chosen printer" })).toBeChecked(),
    );
    expect(screen.getByRole("checkbox", { name: "Select Farm printer" })).not.toBeChecked();
    expect(sendToPrinter).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await waitFor(() =>
      expect(sendToPrinter).toHaveBeenCalledWith(
        9,
        expect.objectContaining({ file_id: 42, start_print: false }),
      ),
    );
    expect(sendToPrinter).toHaveBeenCalledOnce();
  });

  it("does not replace an explicitly denied printer with a default target", async () => {
    const denied = aPrinter({
      id: 9,
      name: "Denied printer",
      access: { ...printer.access, can_print: false },
    });
    renderPanel({ open: true, preselectPrinterId: 9, printers: [printer, denied] });
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Select Farm printer" })).not.toBeChecked(),
    );
    expect(screen.getByRole("checkbox", { name: "Select Denied printer" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send to printer" })).toBeDisabled();
    expect(sendToPrinter).not.toHaveBeenCalled();
  });

  it("retires send preflight with its session", async () => {
    const pending =
      Promise.withResolvers<Awaited<ReturnType<SendToCommands["checkFleetCompatibility"]>>>();
    checkFleetCompatibility.mockReturnValue(pending.promise);
    const deliveries: number[] = [];
    sendToPrinter.mockImplementation(async (id) => {
      deliveries.push(id);
      return queuedJob;
    });
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)!);
    await act(async () => clearLogin());
    await act(async () =>
      pending.resolve({ file_id: 42, requirements: [], nozzle_diameter_mm: null, printers: [] }),
    );
    expect(deliveries).toEqual([]);
  });
  it("retires printer failure feedback with its session", async () => {
    const pending = Promise.withResolvers<PrintJobRead>();
    sendToPrinter.mockReturnValue(pending.promise);
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)!);
    await act(async () => clearLogin());
    await act(async () => pending.reject(new Error("printer offline")));
    expect(screen.queryByText(/Farm printer: printer offline/)).not.toBeInTheDocument();
  });
  it("uses provider format capabilities for BGCODE actions", async () => {
    const textOnly = aPrinter({
      id: 8,
      name: "Text-only printer",
      capabilities: {
        ...printer.capabilities,
        accepted_print_formats: ["gcode_text"],
      },
    });
    const prusa = aPrinter({
      id: 9,
      name: "Core One",
      provider: "prusalink",
      capabilities: {
        ...printer.capabilities,
        accepted_print_formats: ["gcode_text", "bgcode_binary"],
      },
    });
    renderPanel({ filename: "plate.bgcode", printers: [textOnly, prusa] });

    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));

    expect(screen.getByText("Format unsupported")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Select Text-only printer" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Select Core One" })).toBeEnabled();
  });

  it("refreshes fleet projections after a queue command", async () => {
    const app = renderPanel();
    app.client.setQueryData(queryKeys.fleetQueue, []);
    app.client.setQueryData(queryKeys.fleetSummary, {});
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Send to printer/ }));
    await user.click(screen.getByRole("radio", { name: "Add to queue" }));
    await user.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);
    await waitFor(() =>
      expect(app.client.getQueryState(queryKeys.fleetQueue)?.isInvalidated).toBe(true),
    );
    expect(app.client.getQueryState(queryKeys.fleetSummary)?.isInvalidated).toBe(true);
  });
  it("adds selected G-code to least-busy fleet queue", async () => {
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getByRole("radio", { name: "Add to queue" }));
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
    await userEvent.click(screen.getByRole("radio", { name: /Almost empty/ }));

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
    await userEvent.click(screen.getByRole("radio", { name: /Untracked/ }));

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
    await userEvent.click(screen.getByRole("radio", { name: "Add to queue" }));
    await userEvent.click(screen.getByRole("radio", { name: "Choose printer" }));
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
    await userEvent.click(screen.getByRole("radio", { name: "Add to queue" }));
    await userEvent.clear(screen.getByLabelText("Copies"));
    await userEvent.type(screen.getByLabelText("Copies"), "3");
    await userEvent.click(screen.getByRole("radio", { name: "Rush" }));
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
  it("uploads to every printer the user picked", async () => {
    // "Send now" is per-printer, unlike the queue: the fleet does not choose,
    // so each selected machine gets its own upload.
    sendToPrinter.mockResolvedValue(queuedJob);
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)!);

    await waitFor(() =>
      expect(sendToPrinter).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ file_id: 42, start_print: false }),
      ),
    );
  });

  it("keeps a direct send safe unless the user overrode the check", async () => {
    // `allow_mismatch` is how a nozzle-incompatible file gets onto a printer;
    // it must never be the default a plain send carries.
    sendToPrinter.mockResolvedValue(queuedJob);
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)!);

    await waitFor(() =>
      expect(sendToPrinter).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ compatibility_policy: "safe" }),
      ),
    );
  });

  it("reports which printer a partly-failed send did not reach", async () => {
    // "Sent" over a fleet where one machine refused is how somebody walks away
    // from a print that never started.
    sendToPrinter.mockRejectedValue(new Error("printer offline"));
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)!);

    expect(await screen.findByText(/Farm printer: printer offline/)).toBeInTheDocument();
  });

  it("will not send once the user deselects every printer", async () => {
    // A direct send names its target; with none there is nothing to send to.
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await userEvent.click(screen.getByLabelText("Select Farm printer"));

    expect(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)).toBeDisabled();
  });

  it("opens the overview queue action with the queue mode selected", async () => {
    renderPanel({ initialDeliveryMode: "queue", open: true });
    await waitFor(() => expect(screen.getByRole("radio", { name: "Add to queue" })).toBeChecked());
    expect(screen.queryByRole("button", { name: "Send to printer" })).not.toBeInTheDocument();
    expect(enqueueFleetJob).not.toHaveBeenCalled();
  });
});

describe("SendToButtons sliced archives", () => {
  it("shows each named plate with its own estimates", async () => {
    const { client } = renderPanel({ open: true, filename: "project.gcode.3mf", plates: [2, 10] });
    act(() => {
      client.setQueryData(["gcode-plates", 42], {
        plates: [2, 10],
        identities: [{ number: 2, name: "Wall mount" }],
        plate_metadata: [
          { number: 2, metadata: aMetadata({ estimated_time_s: 2465, filament_weight_g: 13.03 }) },
        ],
      });
    });
    expect(
      await screen.findByRole("radio", { name: /Plate 02.*Wall mount.*41m 5s.*13.03 g/ }),
    ).toBeVisible();
  });

  it.each(["send", "queue"] as const)("carries the inspected plate into %s", async (mode) => {
    renderPanel({
      open: true,
      preselectPlate: 10,
      initialDeliveryMode: mode,
      filename: "project.gcode.3mf",
      plates: [2, 10],
    });
    expect(await screen.findByRole("radio", { name: /^Plate 10/ })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /^Plate 02/ }));
    expect(screen.getByRole("radio", { name: /^Plate 02/ })).toBeChecked();
  });
  it("requires a choice when several plates exist", async () => {
    renderPanel({ open: true, filename: "project.gcode.3mf", plates: [2, 10] });
    for (const choice of await screen.findAllByRole("radio", { name: /^Plate / })) {
      expect(choice).not.toBeChecked();
    }
    expect(screen.getByRole("button", { name: "Send to printer" })).toBeDisabled();
    expect(sendToPrinter).not.toHaveBeenCalled();
  });

  it("submits the chosen plate to the printer", async () => {
    sendToPrinter.mockResolvedValue(queuedJob);
    renderPanel({ open: true, filename: "project.GCODE.3MF", plates: [2, 10] });
    await userEvent.click(await screen.findByRole("radio", { name: /^Plate 10/ }));
    await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await waitFor(() =>
      expect(sendToPrinter).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ file_id: 42, plate_index: 10, start_print: false }),
      ),
    );
  });

  it("accepts a corrected quantity after invalid input", async () => {
    renderPanel({ open: true, initialDeliveryMode: "queue" });
    const copies = screen.getByRole("spinbutton", { name: "Copies" });
    const submit = screen.getByRole("button", { name: "Add to queue" });
    for (const value of ["", "0", "-1", "1.5"]) {
      await userEvent.clear(copies);
      if (value) await userEvent.type(copies, value);
      expect(copies).toHaveAttribute("aria-invalid", "true");
      expect(copies).toHaveAccessibleDescription("Enter a whole number of copies, at least 1.");
      expect(submit).toBeDisabled();
    }
    expect(createFleetBatch).not.toHaveBeenCalled();
    expect(enqueueFleetJob).not.toHaveBeenCalled();
    await userEvent.clear(copies);
    await userEvent.type(copies, "2");
    expect(submit).toBeEnabled();
  });

  it("queues the chosen plate", async () => {
    renderPanel({
      open: true,
      initialDeliveryMode: "queue",
      filename: "project.gcode.3mf",
      plates: [2, 10],
    });
    await userEvent.click(await screen.findByRole("radio", { name: /^Plate 02/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
    await waitFor(() =>
      expect(enqueueFleetJob).toHaveBeenCalledWith(
        expect.objectContaining({ file_id: 42, plate_index: 2 }),
      ),
    );
  });

  it("requires a new plate choice after changing revisions", async () => {
    const { client } = renderPanel({
      open: true,
      plates: [2, 10],
      files: [
        aRevision({
          id: 42,
          file_type: "gcode",
          original_filename: "first.gcode.3mf",
          is_recommended: true,
          gcode_revision_number: 1,
        }),
        aRevision({
          id: 43,
          file_type: "gcode",
          original_filename: "second.gcode.3mf",
          is_recommended: false,
          gcode_revision_number: 2,
        }),
      ],
    });
    client.setQueryData(["gcode-plates", 43], { plates: [2, 10] });
    await userEvent.click(await screen.findByRole("radio", { name: /^Plate 10/ }));
    await userEvent.click(screen.getByRole("radio", { name: /Rev 2/ }));
    for (const choice of await screen.findAllByRole("radio", { name: /^Plate / })) {
      expect(choice).not.toBeChecked();
    }
    expect(screen.getByRole("button", { name: "Send to printer" })).toBeDisabled();
    expect(sendToPrinter).not.toHaveBeenCalled();
  });

  it("batches the chosen plate", async () => {
    renderPanel({
      open: true,
      initialDeliveryMode: "queue",
      filename: "project.gcode.3mf",
      plates: [2, 10],
    });
    await userEvent.click(await screen.findByRole("radio", { name: /^Plate 10/ }));
    await userEvent.clear(screen.getByLabelText("Copies"));
    await userEvent.type(screen.getByLabelText("Copies"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
    await waitFor(() =>
      expect(createFleetBatch).toHaveBeenCalledWith(
        expect.objectContaining({ file_id: 42, plate_index: 10, quantity: 3 }),
      ),
    );
  });

  it("selects the only available sliced plate", async () => {
    renderPanel({ open: true, filename: "project.gcode.3mf", plates: [10] });
    expect(await screen.findByRole("radio", { name: /^Plate 10/ })).toBeChecked();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send to printer" })).toBeEnabled(),
    );
  });

  it("blocks unsliced archives", async () => {
    renderPanel({ open: true, filename: "project.gcode.3mf", plates: [] });
    expect(await screen.findByRole("alert")).toHaveTextContent("This 3MF has no sliced G-code");
    expect(screen.getByRole("button", { name: "Send to printer" })).toBeDisabled();
  });
});
