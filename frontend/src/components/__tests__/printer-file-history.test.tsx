/** Files browse the provider's exact paths; Jobs presents reported runs and their evidence. */
import "@testing-library/jest-dom/vitest";
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PrinterFiles, PrinterJobHistory } from "@/components/printer-file-history";
import { printerKeys } from "@/features/printers/queries";
import {
  aModel,
  aPrinter,
  aPrinterFile,
  aPrintJob,
  aRevision,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { PrinterFileRead } from "@/types";

const printer = aPrinter({ id: 4 });
const file = aPrinterFile({
  model_name: "Thermal calibration coupon",
  original_filename: "coupon-original.gcode",
  remote_filename: "thermal-coupon__vault-f20-abc.gcode",
  last_seen_at: "2026-01-01T03:22:06.123456",
});
const nested = aPrinterFile({
  id: 51,
  file_id: null,
  model_id: null,
  model_name: null,
  original_filename: null,
  remote_filename: "Calibration/March/Bed/bed-square.gcode",
  matched_by: "none",
});
const started = "2026-01-01T03:22:00Z";
const completed = aPrintJob({
  id: 7,
  file_id: 20,
  model_id: 1,
  remote_filename: file.remote_filename,
  state: "completed",
  started_at: started,
  finished_at: "2026-01-01T03:23:06",
  printer_id: 4,
  progress: 1,
});
const external = aPrintJob({
  id: 6,
  printer_id: 4,
  state: "failed",
  started_at: null,
  finished_at: "2026-01-01T03:20:05Z",
  source: "external",
  artifact_evidence: "metadata_only",
  external_display_name: "Prime tower",
  external_gcode_file: "other/tower.gcode",
});

function fileProps(files: readonly PrinterFileRead[] = [file, nested]) {
  return {
    printer,
    files,
    startingId: null,
    deletingId: null,
    syncing: false,
    onSync: vi.fn<() => void>(),
    onStart: vi.fn<(file: PrinterFileRead) => void>(),
    onDelete: vi.fn<(file: PrinterFileRead) => void>(),
    onOpenJobs: vi.fn<() => void>(),
  };
}

describe("Printer files", () => {
  it("keeps the four columns for one file", () => {
    renderApp(<PrinterFiles {...fileProps([file])} />);
    const table = screen.getByRole("table", { name: "Printer files" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Name", "Size", "Library", "Last seen"]);
    expect(within(table).getByRole("heading", { name: file.model_name ?? "" })).toBeVisible();
  });

  it("shows the printer’s last-seen instant in the operator’s timezone", () => {
    renderApp(<PrinterFiles {...fileProps([file])} />);
    const instant = new Date("2026-01-01T03:22:06.123Z");
    const full = instant.toLocaleString("en", { dateStyle: "medium", timeStyle: "long" });
    const short = instant.toLocaleString("en", { dateStyle: "short", timeStyle: "short" });
    expect(within(screen.getByRole("table")).getByText(short)).toHaveAttribute("title", full);
    expect(
      within(screen.getByRole("complementary", { name: "Selected file" })).getByText(full),
    ).toHaveAttribute("datetime", instant.toISOString());
  });

  it("selects a file from its size cell", async () => {
    const user = userEvent.setup();
    const other = aPrinterFile({ id: 53, model_name: "Second part", size_bytes: 2048 });
    renderApp(<PrinterFiles {...fileProps([file, other])} />);
    const row = screen.getByRole("row", { name: /Second part/ });
    await user.click(within(row).getByText("2 KB", { exact: true }));
    const button = within(row).getByRole("button", { name: "Second part" });
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(
      within(screen.getByRole("complementary", { name: "Selected file" })).getAllByText(
        "Second part",
      ),
    ).toHaveLength(2);
  });

  it.each([
    ["vault_marker", "Saved Revision ID"],
    ["upload_history", "Upload history"],
    ["filename", "Original filename"],
    ["size", "File size"],
    ["future_provider_code", "Other match method"],
  ])("names the %s match method in the inspector", (method, label) => {
    renderApp(<PrinterFiles {...fileProps([aPrinterFile({ ...file, matched_by: method })])} />);
    const inspector = within(screen.getByRole("complementary", { name: "Selected file" }));
    expect(inspector.getByText(label, { selector: "dd" })).toBeVisible();
  });

  it("traverses nested paths by keyboard with breadcrumb return", async () => {
    const user = userEvent.setup();
    renderApp(<PrinterFiles {...fileProps()} />);
    const folders = within(screen.getByRole("navigation", { name: "Folders" }));
    for (const folder of ["Calibration", "March", "Bed"]) {
      const button = folders.getByRole("button", { name: folder });
      button.focus();
      await user.keyboard("{Enter}");
    }
    const table = screen.getByRole("table", { name: "Printer files" });
    expect(within(table).getByRole("button", { name: "bed-square.gcode" })).toBeVisible();
    const path = within(screen.getByRole("navigation", { name: "Printer file path" }));
    expect(path.getByText("Bed")).toHaveAttribute("aria-current", "location");
    await user.click(path.getByRole("button", { name: "gcodes" }));
    expect(
      within(table).queryByRole("button", { name: "bed-square.gcode" }),
    ).not.toBeInTheDocument();
  });

  it.each(["THERMAL calibration", "COUPON-original.gcode", "thermal-coupon__vault"])(
    "matches printer paths, original names, or Models via %s",
    async (search) => {
      const user = userEvent.setup();
      renderApp(<PrinterFiles {...fileProps()} />);
      const query = screen.getByRole("searchbox", { name: "Search files" });
      await user.type(query, search);
      const table = within(screen.getByRole("table", { name: "Printer files" }));
      expect(table.getByRole("button", { name: "Thermal calibration coupon" })).toBeVisible();
      expect(query).toHaveFocus();
    },
  );

  it("passes the selected remote file unchanged to Start", async () => {
    const user = userEvent.setup();
    const props = fileProps();
    renderApp(<PrinterFiles {...props} />);
    await user.type(screen.getByRole("searchbox", { name: "Search files" }), "bed-square");
    const inspector = within(screen.getByRole("complementary", { name: "Selected file" }));
    expect(inspector.getByText(nested.remote_filename, { exact: true })).toBeVisible();
    await user.click(inspector.getByRole("button", { name: "Start" }));
    expect(props.onStart).toHaveBeenCalledExactlyOnceWith(nested);
  });

  it("disables deletion of the active remote file", () => {
    renderApp(
      <PrinterFiles
        {...fileProps([nested])}
        files={[file]}
        activeFilename={file.remote_filename}
      />,
    );
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("keeps all write controls disabled for a view-only printer grant", () => {
    renderApp(
      <PrinterFiles
        {...fileProps([file])}
        printer={aPrinter({
          ...printer,
          access: printerAccess({ can_admin: false, can_print: false }),
        })}
      />,
    );
    for (const name of ["Sync printer files", "Start", "Delete"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    expect(screen.getByRole("link", { name: "Open Model" })).toHaveAttribute("href", "/models/1");
  });

  it("browses Bambu cache archives without offering a plain G-code start", () => {
    renderApp(
      <PrinterFiles
        {...fileProps([aPrinterFile({ ...file, remote_filename: "plate.3mf" })])}
        printer={aPrinter({ ...printer, provider: "bambu_lan" })}
      />,
    );
    expect(
      within(screen.getByRole("navigation", { name: "Printer file path" })).getByText("/cache"),
    ).toBeVisible();
    expect(screen.getByRole("table", { name: "Printer files" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
  });

  it("offers Jobs as the alternative for a transfer-only provider", async () => {
    const user = userEvent.setup();
    const props = fileProps([]);
    renderApp(
      <PrinterFiles
        {...props}
        printer={aPrinter({
          ...printer,
          provider: "bambu_lan",
          capabilities: printerCapabilities({ can_list_files: false }),
        })}
      />,
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open in Jobs" }));
    expect(props.onOpenJobs).toHaveBeenCalledOnce();
  });

  it("renders empty inventory without file actions", () => {
    renderApp(<PrinterFiles {...fileProps([])} />);
    expect(screen.getByText("No printer files synced yet.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("shows a read error in place of an empty inventory", async () => {
    const app = renderApp(<PrinterFiles {...fileProps([])} />);
    const failure = Promise.withResolvers<PrinterFileRead[]>();
    let query: Promise<PrinterFileRead[] | undefined> | undefined;
    await act(async () => {
      query = app.client
        .fetchQuery({
          queryKey: printerKeys.files(printer.id),
          queryFn: () => failure.promise,
        })
        .catch(() => undefined);
    });
    expect(await screen.findByText("Loading...")).toBeVisible();
    await act(async () => {
      failure.reject(new Error("offline"));
      await query;
    });
    expect(await screen.findByText("Could not load printer files.")).toBeVisible();
  });
});

describe("Printer Jobs", () => {
  it("qualifies a completed external run with no recorded start", () => {
    renderApp(
      <PrinterJobHistory
        printer={printer}
        files={[]}
        jobs={[aPrintJob({ ...external, state: "completed", started_at: null })]}
      />,
    );
    const outcome = "Completed No print start recorded";
    const history = within(screen.getByRole("table", { name: "Print history" }));
    expect(history.getByRole("cell", { name: outcome })).toBeVisible();
    expect(history.getByRole("button", { name: "Prime tower" })).toHaveAccessibleDescription(
      expect.stringContaining(outcome),
    );
    const inspector = within(screen.getByRole("complementary", { name: "Selected run" }));
    expect(inspector.getByText("No print start recorded")).toBeVisible();
  });

  it("joins visible matching inventory without resolving every run", async () => {
    await import("@/components/fleet-add-job");
    const otherFile = aPrinterFile({ id: 54, file_id: 22, model_id: 2, model_name: "Holder" });
    const rows = [
      completed,
      aPrintJob({ ...completed, id: 8, file_id: 22, model_id: 2 }),
      external,
    ];
    const app = renderApp(
      <PrinterJobHistory printer={printer} files={[file, otherFile]} jobs={rows} />,
      { routes: { "GET /api/v1/models/1": json(aModel({ files: [aRevision({ id: 20 })] })) } },
    );
    expect(screen.getByRole("heading", { name: "Thermal calibration coupon" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Holder" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Prime tower" })).toBeVisible();
    expect(await screen.findByRole("button", { name: "Queue again" })).toBeVisible();
    expect(
      app
        .requests()
        .map(({ url }) => url)
        .filter((url) => url.includes("/models/")),
    ).toEqual(["/api/v1/models/1"]);
  });

  it("does not join inventory whose Model id is hidden or different", () => {
    renderApp(
      <PrinterJobHistory
        printer={printer}
        jobs={[completed]}
        files={[aPrinterFile({ ...file, model_id: 42 })]}
      />,
    );
    expect(screen.getByRole("heading", { name: file.remote_filename })).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Thermal calibration coupon" }),
    ).not.toBeInTheDocument();
  });

  it("shows a duration only for runs with both timestamps", () => {
    renderApp(<PrinterJobHistory printer={printer} files={[file]} jobs={[completed, external]} />);
    const table = within(screen.getByRole("table", { name: "Print history" }));
    const timed = table.getByRole("row", { name: /Thermal calibration coupon/ });
    const unknown = table.getByRole("row", { name: /Prime tower/ });
    expect(within(timed).getAllByRole("cell").at(-1)).toHaveTextContent("1m 6s");
    expect(within(unknown).getAllByRole("cell").at(-1)).toHaveTextContent("—");
  });

  it("selects a run from its result cell", async () => {
    const user = userEvent.setup();
    renderApp(<PrinterJobHistory printer={printer} files={[file]} jobs={[completed, external]} />);
    const row = screen.getByRole("row", { name: /Prime tower/ });
    await user.click(within(row).getByRole("cell", { name: "Failed" }));
    const button = within(row).getByRole("button", { name: "Prime tower" });
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute("aria-pressed", "true");
  });

  it("retains the full instant behind a compact row timestamp", () => {
    renderApp(<PrinterJobHistory printer={printer} files={[file]} jobs={[completed]} />);
    const finished = new Date("2026-01-01T03:23:06Z");
    const short = finished.toLocaleString("en", { dateStyle: "short", timeStyle: "short" });
    const full = finished.toLocaleString("en", { dateStyle: "medium", timeStyle: "long" });
    const table = within(screen.getByRole("table", { name: "Print history" }));
    expect(table.getByText(short)).toHaveAttribute("title", full);
    expect(
      within(screen.getByRole("complementary", { name: "Selected run" })).getByText(full),
    ).toHaveAttribute("datetime", finished.toISOString());
  });

  it("selects a run within the current history filters", async () => {
    const user = userEvent.setup();
    renderApp(<PrinterJobHistory printer={printer} files={[file]} jobs={[completed, external]} />);
    const history = within(screen.getByRole("table", { name: "Print history" }));
    const inspector = within(screen.getByRole("complementary", { name: "Selected run" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Outcome" }), "failed");
    expect(
      history.queryByRole("button", { name: "Thermal calibration coupon" }),
    ).not.toBeInTheDocument();
    expect(inspector.getByText("Prime tower")).toBeVisible();
    expect(inspector.queryByRole("link", { name: "Open Model" })).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "Search runs" }), "thermal");
    expect(history.getByText("No runs match your filters.")).toBeVisible();
    expect(inspector.queryByRole("button", { name: "Evidence" })).not.toBeInTheDocument();
  });

  it("keeps run columns in empty history", () => {
    renderApp(<PrinterJobHistory printer={printer} files={[]} jobs={[]} />);
    expect(screen.getByText("No print jobs yet.")).toBeVisible();
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Print",
      "Outcome",
      "Finished",
      "Duration",
    ]);
  });
});
