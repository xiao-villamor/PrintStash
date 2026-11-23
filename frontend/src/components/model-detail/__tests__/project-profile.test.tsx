/** File settings never borrow provider estimates or mark a profile as tested. */
import { screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { renderApp, json } from "@/test-support/render";
import { aProjectProfile } from "@/test-support/factories";
import { ProjectProfile } from "../project-profile";

describe("ProjectProfile", () => {
  it("attributes saved estimates to the selected file", async () => {
    renderApp(<ProjectProfile modelId={1} fileId={100} />, {
      routes: {
        "GET /api/v1/files/100/project-profile": json({
          state: "ready",
          failure_reason: null,
          profile: aProjectProfile({
            name: "0.20mm Standard",
            printer: "P1S",
            materials: ["PLA"],
            layer_height_mm: 0.2,
            nozzle_diameters_mm: [0.4],
            wall_loops: 2,
            infill_percent: 15,
            plates: [{ number: 2, estimated_seconds: 600, filament_grams: 12.5 }],
          }),
        }),
      },
    });
    expect(await screen.findByText("0.20mm Standard")).toBeVisible();
    expect(screen.getByText("10 min · 12.5 g")).toBeVisible();
    expect(screen.getByText("Estimate included in the file")).toBeVisible();
    expect(screen.getByText(/Estimates are not measured print results/)).toBeVisible();
    expect(screen.queryByText("Provider estimate")).not.toBeInTheDocument();
  });
  it("leaves missing facts unavailable", async () => {
    renderApp(<ProjectProfile modelId={1} fileId={101} />, {
      routes: {
        "GET /api/v1/files/101/project-profile": json({
          state: "ready",
          failure_reason: null,
          profile: aProjectProfile(),
        }),
      },
    });
    expect(await screen.findAllByText("Not available in this file")).toHaveLength(7);
    expect(screen.queryByText(/0 min/)).not.toBeInTheDocument();
  });
  it.each([
    ["pending", "Reading project settings…"],
    ["skipped", "This file has no supported project settings."],
    ["failed", "Project settings could not be read. The original file is unchanged."],
    ["disabled", "Project settings have not been checked."],
    ["cancelled", "Project settings have not been checked."],
  ])("explains %s settings", async (state, message) => {
    renderApp(<ProjectProfile modelId={1} fileId={100} />, {
      routes: {
        "GET /api/v1/files/100/project-profile": json({
          state,
          failure_reason: null,
          profile: null,
        }),
      },
    });
    expect(await screen.findByText(message)).toBeVisible();
  });
  it("explains a failed request", async () => {
    renderApp(<ProjectProfile modelId={1} fileId={100} />, {
      routes: {
        "GET /api/v1/files/100/project-profile": () => new Response("", { status: 503 }),
      },
    });
    expect(await screen.findByText("Project settings could not be loaded.")).toBeVisible();
  });
});
