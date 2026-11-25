/* The print overview must ground its recommendation in the available Artifacts and their estimates. */
import { renderApp } from "@/test-support/render";
import { aMetadata, aModelPrintJob, aRevision } from "@/test-support/factories";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { ModelPrintSummary } from "../model-print-summary";

function renderSummary(overrides: Partial<ComponentProps<typeof ModelPrintSummary>>) {
  return renderApp(
    <ModelPrintSummary
      revision={null}
      source={undefined}
      estimates={null}
      onReview={() => {}}
      onFiles={() => {}}
      {...overrides}
    />,
  );
}

describe("ModelPrintSummary", () => {
  it("presents the recommended revision as the route to its print details", async () => {
    const user = userEvent.setup();
    const prepare = vi.fn<() => void>();
    const review = vi.fn<() => void>();
    renderSummary({
      revision: aRevision({
        is_recommended: true,
        gcode_revision_number: 2,
        revision_label: "Wall mount",
        revision_status: "needs_test",
        original_filename: "wall-mount.gcode",
        metadata: aMetadata({
          estimated_time_s: 2096,
          filament_weight_g: 13.7,
          material_type: "PLA",
        }),
      }),
      onPrepare: prepare,
      onReview: review,
    });
    const summary = within(screen.getByRole("region", { name: "Current revision" }));
    expect(summary.getByText("Rev 2 · Wall mount")).toBeVisible();
    expect(summary.getByText("Needs test")).toBeVisible();
    expect(summary.getByText("34m 56s · 13.7 g")).toBeVisible();
    expect(summary.getByText("Recommended", { exact: true })).toBeVisible();
    await user.click(summary.getByRole("button", { name: "Prepare print" }));
    await user.click(summary.getByRole("button", { name: "Rev 2 · Wall mount" }));
    expect(prepare).toHaveBeenCalledOnce();
    expect(review).toHaveBeenCalledOnce();
  });

  it("makes the estimate for each summarized plate reachable", async () => {
    const openSettings = vi.fn<() => void>();
    renderSummary({
      revision: aRevision({ original_filename: "wall-mount.gcode.3mf" }),
      estimates: {
        plates: [1, 2],
        identities: [
          { number: 1, name: "Main plate", object_count: 3 },
          { number: 2, name: "Optional plate", object_count: 1 },
        ],
        plate_metadata: [
          {
            number: 1,
            metadata: { estimated_time_s: 3600, filament_weight_g: 20, filament_cost: null },
          },
          {
            number: 2,
            metadata: { estimated_time_s: 61, filament_weight_g: 2, filament_cost: null },
          },
        ],
      },
      onPlateEstimates: openSettings,
    });
    expect(screen.queryByText("Optional plate")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "2 plates" }));
    expect(openSettings).toHaveBeenCalledOnce();
  });

  it("prioritizes recording once the Model has been sent", () => {
    renderSummary({
      revision: aRevision(),
      onPrepare: () => {},
      onRecordResult: () => {},
      sent: true,
    });
    expect(screen.getByRole("button", { name: "Record result" })).toHaveClass("bg-primary");
    expect(screen.getByRole("button", { name: "Prepare print" })).not.toHaveClass("bg-primary");
  });

  it("offers to mark a Needs test revision known good after a successful print", async () => {
    const mark = vi.fn<() => void>();
    const revision = aRevision({ revision_status: "needs_test" });
    renderSummary({
      revision,
      jobs: [aModelPrintJob({ file_id: revision.id, state: "completed" })],
      onMarkKnownGood: mark,
    });
    await userEvent.click(screen.getByRole("button", { name: "Mark known good" }));
    expect(mark).toHaveBeenCalledOnce();
  });

  it("offers a path to printing an unsliced Model", async () => {
    const user = userEvent.setup();
    const add = vi.fn<() => void>();
    renderSummary({
      source: aRevision({ file_type: "3mf", original_filename: "reindeer.3mf" }),
      onAddRevision: add,
    });
    expect(screen.getByText("No G-code revision")).toBeVisible();
    expect(screen.getByText("reindeer.3mf · 3MF")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Prepare print" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Open in slicer" }));
    expect(screen.getByRole("menuitem", { name: "Download original file" })).toBeVisible();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Add revision" }));
    expect(add).toHaveBeenCalledOnce();
  });
});
