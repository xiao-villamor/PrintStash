/* The inspected revision combines real setup with separately recorded printer
 * results. Unknown history is not zero prints; logging requires an explicit action. */
import "@testing-library/jest-dom/vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderApp } from "@/test-support/render";
import { describe, expect, it, vi } from "vitest";
import { InspectedPrintCard } from "../inspected-print-card";
import { aRevision } from "@/test-support/factories";
import type { ComponentProps } from "react";

function show(overrides: Partial<ComponentProps<typeof InspectedPrintCard>> = {}) {
  const onLogPrint = vi.fn<(state: "completed" | "failed") => void>();
  const onSend = vi.fn<(mode: "send" | "queue") => void>();
  renderApp(
    <InspectedPrintCard
      file={aRevision({ id: 12 })}
      canEdit
      canSend
      revisionCount={2}
      recommended={null}
      jobs={null}
      onRevisions={() => {}}
      onSettings={() => {}}
      onHistory={() => {}}
      onLogPrint={onLogPrint}
      onSend={onSend}
      onInspect={() => {}}
      onAddRevision={() => {}}
      onCompare={() => {}}
      {...overrides}
    />,
  );
  return { onLogPrint, onSend };
}

describe("InspectedPrintCard", () => {
  it("shows the inspected revision's per-plate estimates", () => {
    show({
      plateEstimates: {
        plates: [1, 2],
        identities: [],
        plate_metadata: [
          {
            number: 1,
            metadata: { estimated_time_s: 27862, filament_weight_g: 419.18, filament_cost: null },
          },
          {
            number: 2,
            metadata: { estimated_time_s: 2465, filament_weight_g: 13.03, filament_cost: null },
          },
        ],
      },
      selectedSlicedPlate: 2,
    });
    const plate = screen.getByRole("region", { name: "Plate 02" });
    expect(within(plate).getByText("41m 5s")).toBeVisible();
    expect(within(plate).getByText("Selected plate")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Print estimate" })).not.toBeInTheDocument();
  });

  it("groups only the inspected file's recorded results by printer", () => {
    show({
      jobs: [
        { file_id: 12, printer_name: "X1C", state: "completed" },
        { file_id: 12, printer_name: "X1C", state: "completed" },
        { file_id: 12, printer_name: "MK4", state: "failed" },
        { file_id: 11, printer_name: "Other", state: "completed" },
      ],
    });
    const table = screen.getByRole("table");
    expect(within(table).getByRole("row", { name: "X1C Successful 2" })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: "MK4 Failed 1" })).toBeInTheDocument();
    expect(within(table).queryByText("Other")).not.toBeInTheDocument();
  });
  it("does not claim zero prints when history is unavailable", () => {
    show();
    const history = screen.getByRole("region", { name: "Recorded prints" });
    expect(within(history).getByText("Not recorded")).toBeInTheDocument();
    expect(
      within(history).queryByText("No prints recorded for this revision."),
    ).not.toBeInTheDocument();
  });
  it("shows a confirmed empty print history", () => {
    show({ jobs: [] });
    expect(screen.getByText("No prints recorded for this revision.")).toBeInTheDocument();
  });
  it("starts a print record only after explicit selection", () => {
    const { onLogPrint } = show();
    expect(onLogPrint).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Successful" }));
    expect(onLogPrint).toHaveBeenCalledWith("completed");
  });
  it("opens queue delivery from its separate action", () => {
    const { onSend } = show();
    fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));
    expect(onSend).toHaveBeenCalledWith("queue");
  });
  it("hides printing actions without printer access", () => {
    show({ canEdit: false, canSend: false });
    expect(screen.queryByRole("button", { name: "Send to printer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Successful" })).not.toBeInTheDocument();
  });
  it("explains that a separate G-code revision is missing", () => {
    show({ file: null, revisionCount: 0, canEdit: false });
    expect(
      screen.getByText(
        "No G-code revision yet. Add a revision to capture the settings that worked.",
      ),
    ).toBeVisible();
  });
});
