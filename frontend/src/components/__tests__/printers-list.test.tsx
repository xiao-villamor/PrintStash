import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrintersPage } from "@/components/printers-list";

vi.mock("@/lib/api", () => ({
  createPrinter: vi.fn().mockResolvedValue({}),
  deletePrinter: vi.fn(),
}));
vi.mock("@/lib/queries", () => ({
  usePrinters: () => ({ data: [], isLoading: false, error: null, refetch: vi.fn() }),
}));
vi.mock("@/lib/use-require-auth", () => ({
  useRequireAuth: () => ({ isAuthenticated: true, showAuthRequiredToast: vi.fn() }),
}));
vi.mock("@/lib/navigation", () => ({
  Link: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props}>{children}</a>
  ),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { createPrinter } from "@/lib/api";

beforeEach(() => vi.clearAllMocks());

async function openForm() {
  render(<PrintersPage />);
  await userEvent.click(screen.getByRole("button", { name: /add printer/i }));
}

describe("printer setup", () => {
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
    await userEvent.type(screen.getByLabelText("Moonraker URL"), "http://neptune.local:7125");
    await userEvent.click(screen.getAllByRole("button", { name: /^add printer$/i }).at(-1)!);

    await waitFor(() => expect(createPrinter).toHaveBeenCalledWith(expect.objectContaining({
      provider: "moonraker",
      provider_variant: "elegoo_neptune4",
      moonraker_url: "http://neptune.local:7125",
    })));
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
});
