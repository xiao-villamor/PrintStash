import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { SendToButtons } from "@/components/model-detail/send-to-buttons";
import type { PrinterRead } from "@/types";

const { enqueueFleetJob, mockUsePrinters } = vi.hoisted(() => ({
  enqueueFleetJob: vi.fn(),
  mockUsePrinters: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  enqueueFleetJob,
  sendToPrinter: vi.fn(),
}));
vi.mock("@/lib/queries", () => ({
  usePrinters: () => mockUsePrinters(),
  useSpoolmanStatus: () => ({ data: { enabled: false } }),
  useSpools: () => ({ data: [] }),
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
