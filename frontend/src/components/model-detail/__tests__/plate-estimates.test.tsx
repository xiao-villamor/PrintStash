/** Each sliced plate retains its own estimate and selection identity. */
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlateEstimates } from "../plate-estimates";
import type { GcodePlates } from "@/lib/api/plates";

const plates: GcodePlates = {
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
};

describe("PlateEstimates", () => {
  it("shows precise estimates under their own plates", () => {
    render(<PlateEstimates data={plates} selected={2} />);
    const first = screen.getByRole("region", { name: "Plate 01" });
    const second = screen.getByRole("region", { name: "Plate 02" });
    expect(within(first).getByText("7h 44m 22s")).toBeVisible();
    expect(within(first).getByText("419.18 g")).toBeVisible();
    expect(within(second).getByText("41m 5s")).toBeVisible();
    expect(within(second).getByText("13.03 g")).toBeVisible();
    expect(within(second).getByText("Selected plate")).toBeVisible();
    expect(within(first).queryByText("Selected plate")).not.toBeInTheDocument();
  });

  it("identifies missing estimates for each plate without inventing totals", () => {
    render(<PlateEstimates data={{ ...plates, plate_metadata: [] }} selected={null} />);
    expect(
      within(screen.getByRole("region", { name: "Plate 01" })).getAllByText("Not recorded"),
    ).toHaveLength(3);
    expect(
      within(screen.getByRole("region", { name: "Plate 02" })).getAllByText("Not recorded"),
    ).toHaveLength(3);
    expect(screen.queryByText("Selected plate")).not.toBeInTheDocument();
  });
});
