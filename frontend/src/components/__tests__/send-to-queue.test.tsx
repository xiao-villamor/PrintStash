import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { SendToButtons } from "@/components/model-detail/send-to-buttons";
import type { PrinterRead, SpoolRead } from "@/types";

const { checkFleetCompatibility, createFleetBatch, enqueueFleetJob, mockUsePrinters, mockUseSpoolmanStatus, mockUseSpools } = vi.hoisted(() => ({
  checkFleetCompatibility: vi.fn(),
  createFleetBatch: vi.fn(),
  enqueueFleetJob: vi.fn(),
  mockUsePrinters: vi.fn(),
  mockUseSpoolmanStatus: vi.fn(() => ({ data: { enabled: false } })),
  mockUseSpools: vi.fn(() => ({ data: [] as SpoolRead[] })),
}));
vi.mock("@/lib/api", () => ({
  checkFleetCompatibility,
  createFleetBatch,
  enqueueFleetJob,
  sendToPrinter: vi.fn(),
}));
vi.mock("@/lib/queries", () => ({
  usePrinters: () => mockUsePrinters(),
  useSpoolmanStatus: () => mockUseSpoolmanStatus(),
  useSpools: () => mockUseSpools(),
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
vi.mock("@/lib/task-center", () => ({ createTask: vi.fn(), updateTask: vi.fn() }));
vi.mock("@/lib/navigation", () => ({
  Link: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props}>{children}</a>,
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

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

beforeEach(() => {
  enqueueFleetJob.mockReset();
  enqueueFleetJob.mockResolvedValue({ id: 1 });
  createFleetBatch.mockReset();
  createFleetBatch.mockResolvedValue({ id: 1, jobs: [] });
  checkFleetCompatibility.mockReset();
  checkFleetCompatibility.mockResolvedValue({ file_id: 42, requirements: [], nozzle_diameter_mm: null, printers: [] });
  mockUsePrinters.mockReturnValue({ data: [printer], isLoading: false, error: null });
  mockUseSpoolmanStatus.mockReturnValue({ data: { enabled: false } });
  mockUseSpools.mockReturnValue({ data: [] });
});

it("adds selected G-code to least-busy fleet queue", async () => {
  render(
    <SendToButtons
      gcodeFiles={[{
        id: 42,
        original_filename: "cube.gcode",
        version: 1,
        gcode_revision_number: 1,
        revision_label: null,
        is_recommended: true,
        metadata: null,
      }]}
      printerFiles={[]}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await userEvent.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);

  await waitFor(() => expect(enqueueFleetJob).toHaveBeenCalledWith(expect.objectContaining({
    file_id: 42,
    strategy: "least_busy",
    printer_id: undefined,
  })));
});

it("warns when the selected spool doesn't have enough filament left, but doesn't block sending", async () => {
  mockUseSpoolmanStatus.mockReturnValue({ data: { enabled: true } });
  mockUseSpools.mockReturnValue({
    data: [
      { id: 1, filament_id: null, name: "Almost empty", filament_name: null, vendor_name: null, material: null, color_hex: null, remaining_weight: 10, used_weight: null, archived: false, location: null },
    ],
  });

  render(
    <SendToButtons
      gcodeFiles={[{
        id: 42,
        original_filename: "cube.gcode",
        version: 1,
        gcode_revision_number: 1,
        revision_label: null,
        is_recommended: true,
        metadata: { filament_weight_g: 250 } as import("@/types").MetadataRead,
      }]}
      printerFiles={[]}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.selectOptions(screen.getByLabelText("Spool"), "1");

  expect(await screen.findByText(/needs ~250g.*10g left/)).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Send to printer" }).at(-1)).not.toBeDisabled();
});

it("warns when the spool has no tracked remaining weight instead of assuming it's plenty", async () => {
  mockUseSpoolmanStatus.mockReturnValue({ data: { enabled: true } });
  mockUseSpools.mockReturnValue({
    data: [
      { id: 1, filament_id: null, name: "Untracked", filament_name: null, vendor_name: null, material: null, color_hex: null, remaining_weight: null, used_weight: null, archived: false, location: null },
    ],
  });

  render(
    <SendToButtons
      gcodeFiles={[{
        id: 42,
        original_filename: "cube.gcode",
        version: 1,
        gcode_revision_number: 1,
        revision_label: null,
        is_recommended: true,
        metadata: { filament_weight_g: 250 } as import("@/types").MetadataRead,
      }]}
      printerFiles={[]}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.selectOptions(screen.getByLabelText("Spool"), "1");

  expect(await screen.findByText(/no tracked remaining weight/)).toBeInTheDocument();
});

it("confirms a known manual mismatch and records the override policy", async () => {
  checkFleetCompatibility.mockResolvedValue({
    file_id: 42,
    requirements: [{ tool_index: 0, material_type: "PLA", color_hex: null }],
    nozzle_diameter_mm: 0.4,
    printers: [{ printer_id: 7, verdict: "mismatch", reasons: ["material_type_mismatch"], missing_materials: ["pla"], color_advisories: [] }],
  });
  render(<SendToButtons gcodeFiles={[{ id: 42, original_filename: "cube.gcode", version: 1, gcode_revision_number: 1, revision_label: null, is_recommended: true, metadata: null }]} printerFiles={[]} />);

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await userEvent.selectOptions(screen.getByLabelText("Routing"), "manual");
  await userEvent.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);
  expect(await screen.findByText("Print with a known material mismatch?")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Print anyway" }));

  await waitFor(() => expect(enqueueFleetJob).toHaveBeenCalledWith(expect.objectContaining({
    compatibility_policy: "allow_mismatch",
    printer_id: 7,
  })));
});

it("creates an atomic batch when copies is greater than one", async () => {
  render(<SendToButtons gcodeFiles={[{ id: 42, original_filename: "cube.gcode", version: 1, gcode_revision_number: 1, revision_label: null, is_recommended: true, metadata: null }]} printerFiles={[]} />);

  await userEvent.click(screen.getByRole("button", { name: "Send to printer" }));
  await userEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await userEvent.clear(screen.getByLabelText("Copies"));
  await userEvent.type(screen.getByLabelText("Copies"), "3");
  await userEvent.selectOptions(screen.getByLabelText("Priority"), "rush");
  await userEvent.type(screen.getByLabelText("Printer group"), "Workshop");
  await userEvent.click(screen.getAllByRole("button", { name: "Add to queue" }).at(-1)!);

  await waitFor(() => expect(createFleetBatch).toHaveBeenCalledWith(expect.objectContaining({
    file_id: 42,
    quantity: 3,
    priority: "rush",
    target_group: "Workshop",
  })));
});
