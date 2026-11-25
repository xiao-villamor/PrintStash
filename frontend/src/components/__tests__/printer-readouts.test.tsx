/*
 * The strip repeats only what a printer actually reports. A reading it does not
 * send must not appear as a zero, and a printer that sends none shows no strip.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PrinterReadouts } from "@/components/printer-readouts";

describe("PrinterReadouts", () => {
  it("lists the reported temperatures and the fan as whole numbers", () => {
    render(
      <PrinterReadouts
        snapshot={{
          extruder: { temperature: 219.6 },
          heater_bed: { temperature: 55.2 },
          fan: { speed: 0.5 },
        }}
      />,
    );

    const strip = within(screen.getByRole("group", { name: "Live readings" }));
    expect(strip.getByText("Hotend").parentElement).toHaveTextContent("220 °C");
    expect(strip.getByText("Bed").parentElement).toHaveTextContent("55 °C");
    expect(strip.getByText("Part fan").parentElement).toHaveTextContent("50%");
  });

  it("omits a reading the printer does not report", () => {
    render(<PrinterReadouts snapshot={{ extruder: { temperature: 22 } }} />);

    expect(screen.getByText("Hotend")).toBeVisible();
    expect(screen.queryByText("Bed")).not.toBeInTheDocument();
    expect(screen.queryByText("Part fan")).not.toBeInTheDocument();
  });

  it("renders nothing when no reading is reported", () => {
    const { container } = render(<PrinterReadouts snapshot={{ extruder: { temperature: null } }} />);

    expect(container).toBeEmptyDOMElement();
  });
});
