import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { SendToButtons } from "@/components/model-detail/send-to-buttons";
import type { PrinterRead, SpoolRead } from "@/types";

const { enqueueFleetJob, mockUsePrinters, mockUseSpoolmanStatus, mockUseSpools } = vi.hoisted(() => ({
  enqueueFleetJob: vi.fn(),
  mockUsePrinters: vi.fn(),
  mockUseSpoolmanStatus: vi.fn(() => ({ data: { enabled: false } })),
  mockUseSpools: vi.fn(() => ({ data: [] as SpoolRead[] })),
}));
vi.mock("@/lib/api", () => ({
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
