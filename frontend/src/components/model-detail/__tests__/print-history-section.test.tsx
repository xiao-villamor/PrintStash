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
    geometric_sha256: null,
    revision_label: "PLA draft",
    revision_status: null,
    revision_notes: null,
    is_recommended: true,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    tags: [],
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

function renderHistory(
  options: RenderAppOptions & {
    jobs?: ModelPrintJobRead[];
    initialEntry?: { fileId: number; state: "completed" | "failed" };
  } = {},
) {
  const { jobs = [aPrintJob()], initialEntry, seed = [], routes = {}, ...rest } = options;
  const onJobCreated = vi.fn<(job: ModelPrintJobRead) => void>();
  const result = renderApp(
    <PrintHistorySection
      jobs={jobs}
      initialEntry={initialEntry}
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
  await user.click(await screen.findByRole("button", { name: "Record result" }));
}

async function chooseVoron(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /^Printer$/ }));
  await user.click(await screen.findByRole("option", { name: /^Voron$/ }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrintHistorySection", () => {
  it("labels a partial stopped-run cost in its original currency", async () => {
    renderHistory({
      jobs: [
        aPrintJob({
          state: "cancelled",
          production_cost: {
            currency: "EUR",
            total: 0.25,
            material: null,
            machine: 0.25,
            estimated: true,
          },
        }),
      ],
    });

    expect(await screen.findByText("Estimated partial cost · €0.25")).toBeVisible();
    expect(screen.getByText("Machine · €0.25")).toBeVisible();
  });

  describe("listing prints", () => {
    it("lists the prints already recorded", async () => {
      renderHistory();

      expect(await screen.findAllByText(/Voron/)).not.toHaveLength(0);
      expect(screen.getByRole("heading", { name: /Print history · 1/ })).toBeVisible();
      expect(screen.getByText("Success")).toBeVisible();
    });

    it("says so when nothing has been printed", async () => {
      renderHistory({ jobs: [] });

      expect(await screen.findByText("No print history yet.")).toBeVisible();
      expect(screen.getByText(/Record a result after each print/)).toBeVisible();
      expect(screen.getByRole("heading", { name: /Print history · 0/ })).toBeVisible();
    });
  });

  describe("choosing how to record one", () => {
    it("opens on manual entry", async () => {
      const user = userEvent.setup();
      renderHistory();

      await openForm(user);

      expect(screen.getByRole("dialog", { name: "Record result" })).toBeInTheDocument();
    });

    it("offers importing from a printer instead", async () => {
      const user = userEvent.setup();
      renderHistory();
      await user.click(screen.getByRole("button", { name: "Import from printer" }));

      expect(screen.getByRole("dialog", { name: "Import from printer" })).toBeInTheDocument();
    });
  });

  describe("logging a print by hand", () => {
    it("POSTs the print the user described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs": json(aPrintJob({ id: 31 })) },
      });
      await openForm(user);
      await user.click(screen.getByRole("radio", { name: "Success" }));
      await chooseVoron(user);

      await user.click(screen.getByRole("button", { name: "Save record" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("print-jobs"))).toBe(
          true,
        ),
      );
    });

    it("names the next missing field and keeps Save record disabled until it is met", async () => {
      const user = userEvent.setup();
      renderHistory();
      await openForm(user);
      const save = screen.getByRole("button", { name: "Save record" });

      expect(save).toBeDisabled();
      expect(screen.getByText("Choose the observed outcome.")).toBeVisible();
      expect(save).toHaveAccessibleDescription("Choose the observed outcome.");

      await user.click(screen.getByRole("radio", { name: "Failed" }));
      expect(screen.getByText("Choose the printer that ran it.")).toBeVisible();
      expect(save).toBeDisabled();

      await chooseVoron(user);
      expect(save).toBeEnabled();
      expect(screen.queryByText("Choose the printer that ran it.")).toBeNull();
    });

    it("sends the outcome, revision and failure note the user chose", async () => {
      const user = userEvent.setup();
      const view = renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs": json(aPrintJob({ id: 31 })) },
      });
      await openForm(user);
      await user.click(screen.getByRole("radio", { name: "Cancelled" }));
      await chooseVoron(user);
      await user.type(screen.getByRole("textbox", { name: "What went wrong" }), "Out of filament");
      await user.click(screen.getByRole("button", { name: "Save record" }));

      await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
      expect(JSON.parse(view.requestsWithMethod("POST")[0].body ?? "{}")).toMatchObject({
        printer_id: 4,
        file_id: 20,
        state: "cancelled",
        error: "Out of filament",
      });
    });

    it("tells the page about the print it recorded", async () => {
      // The list above is the caller's state, so a job the section keeps to
      // itself is one the user cannot see until a reload.
      const user = userEvent.setup();
      const { onJobCreated } = renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs": json(aPrintJob({ id: 31 })) },
      });
      await openForm(user);
      await user.click(screen.getByRole("radio", { name: "Success" }));
      await chooseVoron(user);

      await user.click(screen.getByRole("button", { name: "Save record" }));

      await waitFor(() => expect(onJobCreated).toHaveBeenCalled());
    });
  });

  describe("failure note keyboard submission", () => {
    it("focuses the failure note from a revision shortcut", async () => {
      renderHistory({ initialEntry: { fileId: 20, state: "failed" } });

      expect(await screen.findByRole("textbox", { name: "What went wrong" })).toHaveFocus();
      expect(screen.getByRole("radio", { name: "Failed" })).toBeChecked();
    });

    it("saves the selected revision failure note with Control Enter", async () => {
      const user = userEvent.setup();
      const view = renderHistory({
        initialEntry: { fileId: 20, state: "failed" },
        routes: {
          "POST /api/v1/models/1/print-jobs": json(
            aPrintJob({ id: 31, state: "failed", error: "Corner lifted" }),
          ),
        },
      });
      await chooseVoron(user);
      await user.type(screen.getByRole("textbox", { name: "What went wrong" }), "Corner lifted");

      await user.keyboard("{Control>}{Enter}{/Control}");

      await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
      expect(JSON.parse(view.requestsWithMethod("POST")[0].body ?? "{}")).toMatchObject({
        printer_id: 4,
        file_id: 20,
        state: "failed",
        error: "Corner lifted",
      });
    });

    it("keeps an incomplete failure note as a draft", async () => {
      const user = userEvent.setup();
      const view = renderHistory({ initialEntry: { fileId: 20, state: "failed" } });
      await user.type(
        await screen.findByRole("textbox", { name: "What went wrong" }),
        "Corner lifted",
      );

      await user.keyboard("{Control>}{Enter}{/Control}");

      expect(view.requestsWithMethod("POST")).toEqual([]);
      expect(screen.getByRole("textbox", { name: "What went wrong" })).toHaveValue("Corner lifted");
      expect(screen.getByRole("button", { name: "Save record" })).toBeDisabled();
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
      await user.click(screen.getByRole("button", { name: "Import from printer" }));
      await chooseVoron(user);

      await user.click(screen.getByRole("button", { name: /Fetch & Import/i }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("import-printer/4")),
        ).toBe(true),
      );
    });

    it("offers manual entry when no printer can supply history", async () => {
      const user = userEvent.setup();
      renderHistory({
        seed: [[queryKeys.printers, []]],
        routes: { "GET /api/v1/printers": json([]) },
      });
      await user.click(screen.getByRole("button", { name: "Import from printer" }));
      await user.click(await screen.findByRole("button", { name: "Add record manually" }));
      expect(screen.getByRole("dialog", { name: "Record result" })).toBeVisible();
    });
    it("cannot import until a printer is chosen", async () => {
      // The import is scoped to one machine, so "which printer?" has no default.
      const user = userEvent.setup();
      renderHistory();
      await user.click(screen.getByRole("button", { name: "Import from printer" }));

      expect(screen.getByRole("button", { name: /Fetch & Import/i })).toBeDisabled();
    });

    it("says so when the printer had nothing new", async () => {
      // An import that silently does nothing is indistinguishable from one that
      // failed.
      const user = userEvent.setup();
      renderHistory({
        routes: { "POST /api/v1/models/1/print-jobs/import-printer/4": json([]) },
      });
      await user.click(screen.getByRole("button", { name: "Import from printer" }));
      await chooseVoron(user);

      await user.click(screen.getByRole("button", { name: /Fetch & Import/i }));

      expect(await screen.findByText("No new jobs to import")).toBeInTheDocument();
    });
  });
});
