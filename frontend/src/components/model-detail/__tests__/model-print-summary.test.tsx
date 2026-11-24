import { renderApp } from "@/test-support/render";
import { aMetadata, aRevision } from "@/test-support/factories";
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
      selectedPlate={null}
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
    const summary = within(screen.getByRole("region", { name: "Print status" }));
    expect(summary.getByText("Rev 2 · Wall mount")).toBeVisible();
    expect(summary.getByText("Needs test")).toBeVisible();
    expect(summary.getByText("PLA · 34m 56s · 13.7 g")).toBeVisible();
    expect(summary.getByRole("img", { name: "Recommended revision" })).toBeVisible();
    await user.click(summary.getByRole("button", { name: "Prepare print" }));
    await user.click(summary.getByRole("button", { name: "Rev 2 · Wall mount" }));
    expect(prepare).toHaveBeenCalledOnce();
    expect(review).toHaveBeenCalledOnce();
  });

  it("shows each plate's estimate and highlights the selected plate", () => {
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
      selectedPlate: 2,
    });
    const main = within(screen.getByRole("region", { name: "Plate 01" }));
    const selected = within(screen.getByRole("region", { name: "Plate 02" }));
    expect(main.getByText("Main plate")).toBeVisible();
    expect(main.queryByText("Selected plate")).toBeNull();
    expect(selected.getByText("Selected plate")).toBeVisible();
    expect(selected.getByText("1m 1s")).toBeVisible();
    expect(selected.getByText("2 g")).toBeVisible();
  });

  it("offers the source file and Add revision on an unsliced model", async () => {
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
