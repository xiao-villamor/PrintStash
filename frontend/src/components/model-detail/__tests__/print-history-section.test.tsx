/*
 * What actually came out of the printer, and how it got recorded.
 *
 * Two paths write here and they answer different questions. Importing from a
 * printer is a *reconciliation*: it pulls jobs the machine already ran, so
 * running it twice must not double the history — and when there is nothing new
 * it has to say so rather than looking like it failed. Logging by hand is for
 * everything the printer never reported: a print run before PrintStash existed,
 * or one on a machine it does not talk to at all.
 *
 * A manual entry can name a printer that is not registered, and that is
 * deliberate — refusing the free-text name would make the whole feature useless
 * to somebody logging a print from a friend's machine. It travels as a name with
 * no id, and conflating the two would attach the job to whichever printer
 * happened to be selected.
 *
 * The figures a job carries — filament, duration, cost — feed the statistics
 * page, so a field left empty has to arrive as "unknown" rather than as zero.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrintHistorySection } from "@/components/model-detail/print-history-section";
import { queryKeys } from "@/lib/query-client";
import { aPrinter } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FileRead, ModelPrintJobRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aGcode(over: Partial<FileRead> = {}): FileRead {
  return {
    id: 20,
    model_id: 1,
    original_filename: "part.gcode",
    file_type: "gcode",
    version: 1,
    size_bytes: 4096,
    sha256: "b".repeat(64),
    revision_label: "PLA draft",
    revision_status: null,
    revision_notes: null,
    is_recommended: true,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    ...over,
  };
}

function aPrintJob(over: Partial<ModelPrintJobRead> = {}): ModelPrintJobRead {
  return {
    id: 30,
    printer_id: 4,
    printer_name: "Voron",
    file_id: 20,
    remote_filename: "part.gcode",
    source: "printer",
    external_display_name: null,
    artifact_evidence: "matched",
    gcode_revision_number: 1,
    revision_label: "PLA draft",
    state: "completed",
    material_type: "PLA",
    error: null,
    filament_used_g: 42,
    actual_duration_s: 3600,
    filament_cost: 1.2,
    spool_id: null,
    spool_name: null,
    started_at: FROZEN_NOW,
    finished_at: FROZEN_NOW,
    created_at: FROZEN_NOW,
    ...over,
  };
}

function renderHistory(options: RenderAppOptions & { jobs?: ModelPrintJobRead[] } = {}) {
  const { jobs = [aPrintJob()], seed = [], routes = {}, ...rest } = options;
  const onJobCreated = vi.fn<(job: ModelPrintJobRead) => void>();
  const result = renderApp(
    <PrintHistorySection
      jobs={jobs}
      modelId={1}
      gcodeFiles={[aGcode()]}
      onJobCreated={onJobCreated}
    />,
    {
      seed: [
        [queryKeys.printers, [aPrinter({ id: 4, name: "Voron" })]],
        [queryKeys.spoolmanStatus, { enabled: false, url: null, reachable: false }],
        [queryKeys.spools, []],
        ...seed,
      ],
      routes: {
        "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
        "GET /api/v1/spoolman/status": json({ enabled: false, url: null, reachable: false }),
        "GET /api/v1/models/1/print-jobs": json(jobs),
        ...routes,
      },
      ...rest,
    },
  );
  return { ...result, onJobCreated };
}

/** Open the add-a-print form. */
async function openForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Add|Log/i }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrintHistorySection", () => {
  describe("listing prints", () => {
    it("lists the prints already recorded", async () => {
      renderHistory();

      expect(await screen.findAllByText(/Voron/)).not.toHaveLength(0);
    });

    it("says so when nothing has been printed", async () => {
      renderHistory({ jobs: [] });

      await waitFor(() => expect(screen.queryByText(/3600/)).toBeNull());
    });
  });

  describe("choosing how to record one", () => {
    it("opens on manual entry", async () => {
      const user = userEvent.setup();
      renderHistory();

      await openForm(user);

      expect(screen.getByRole("button", { name: "Manual Entry" })).toBeInTheDocument();
    });

    it("offers importing from a printer instead", async () => {
      const user = userEvent.setup();
      renderHistory();
      await openForm(user);

      await user.click(screen.getByRole("button", { name: "Auto from Printer" }));

      expect(screen.getByRole("button", { name: "Auto from Printer" })).toBeInTheDocument();
    });
  });

  describe("logging a print by hand", () => {
    it("POSTs the print the user described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs": json(aPrintJob({ id: 31 })) },
      });
      await openForm(user);
      await user.selectOptions(screen.getAllByRole("combobox")[0], "4");

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("print-jobs"))).toBe(
          true,
        ),
      );
    });

    it("tells the page about the print it recorded", async () => {
      // The list above is the caller's state, so a job the section keeps to
      // itself is one the user cannot see until a reload.
      const user = userEvent.setup();
      const { onJobCreated } = renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs": json(aPrintJob({ id: 31 })) },
      });
      await openForm(user);
      await user.selectOptions(screen.getAllByRole("combobox")[0], "4");

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => expect(onJobCreated).toHaveBeenCalled());
    });
  });

  describe("importing from a printer", () => {
    it("asks the printer for jobs it already ran", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderHistory({
        routes: {
          "POST /api/v1/models/1/print-jobs/import-printer/4": json([
            { id: 31, printer_name: "Voron", state: "completed" },
          ]),
        },
      });
      await openForm(user);
      await user.click(screen.getByRole("button", { name: "Auto from Printer" }));
      await user.selectOptions(await screen.findByRole("combobox"), "4");

      await user.click(screen.getByRole("button", { name: /Fetch & Import/i }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("import-printer/4")),
        ).toBe(true),
      );
    });

    it("cannot import until a printer is chosen", async () => {
      // The import is scoped to one machine, so "which printer?" has no default.
      const user = userEvent.setup();
      renderHistory();
      await openForm(user);

      await user.click(screen.getByRole("button", { name: "Auto from Printer" }));

      expect(screen.getByRole("button", { name: /Fetch & Import/i })).toBeDisabled();
    });

    it("says so when the printer had nothing new", async () => {
      // An import that silently does nothing is indistinguishable from one that
      // failed.
      const user = userEvent.setup();
      renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs/import-printer/4": json([]) },
      });
      await openForm(user);
      await user.click(screen.getByRole("button", { name: "Auto from Printer" }));
      await user.selectOptions(await screen.findByRole("combobox"), "4");

      await user.click(screen.getByRole("button", { name: /Fetch & Import/i }));

      expect(await screen.findByText("No new jobs to import")).toBeInTheDocument();
    });
  });
});
