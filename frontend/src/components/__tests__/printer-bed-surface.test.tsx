/** Bed measurements require an eligible idle machine and preserve operator feedback through readback or recovery. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it } from "vitest";

import { PrinterBedSurface } from "@/components/printer-bed-surface";
import type { BedMesh } from "@/features/printers/inspection";
import { userMessage } from "@/lib/errors";
import { aPrinter, printerAccess } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

const bedMeshPath = "GET /api/v1/printers/8/bed-mesh";
const measurementUrl = "/api/v1/printers/8/bed-mesh/measurement";
const measurementAvailability = `GET ${measurementUrl}`;
const measurementPost = `POST ${measurementUrl}`;
const activeMesh: BedMesh = {
  state: "active",
  profile_name: "first-layer",
  mesh_min: [10, 20],
  mesh_max: [110, 120],
  probed_matrix: [
    [0.07, -0.02, 0.01],
    [0.005, 0.012, -0.01],
  ],
  variation_mm: 0.09,
};

type Control = NonNullable<ComponentProps<typeof PrinterBedSurface>["control"]>;

function readyControl(override: Partial<Control> = {}): Control {
  return {
    printer: aPrinter({ id: 8 }),
    snapshot: {
      webhooks: { state: "ready" },
      print_stats: { state: "standby" },
      toolhead: { homed_axes: "xyz" },
      idle_timeout: { state: "Ready" },
    },
    live: true,
    busy: false,
    async run(_key, command, _success, report) {
      try {
        await command();
        report("success", "Bed measured");
      } catch (error) {
        report("error", userMessage(error));
      }
    },
    ...override,
  };
}

const automaticBed = {
  [bedMeshPath]: () => json({ state: "inactive" }),
  [measurementAvailability]: () => json({ supported: true }),
};

async function openMeasurement() {
  await userEvent.click(await screen.findByRole("button", { name: "Measure bed" }));
  return screen.getByRole("dialog", { name: "Measure bed" });
}

describe("printer bed surface", () => {
  it("reads the real mesh as a numeric top view", async () => {
    renderApp(<PrinterBedSurface printerId={8} />, {
      routes: { [bedMeshPath]: () => json(activeMesh) },
    });
    const table = await screen.findByRole("table", { name: "Probe heights in millimetres" });
    const [header, back, front] = within(table).getAllByRole("row");

    expect(
      within(header)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual(["Y ↑ / X → (mm)", "10", "60", "110"]);
    expect(within(back).getByRole("rowheader", { name: "120" })).toBeInTheDocument();
    expect(within(front).getByRole("rowheader", { name: "20" })).toBeInTheDocument();
    expect(
      within(back)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["0.005", "0.012", "-0.01"]);
    expect(
      within(front)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["0.07", "-0.02", "0.01"]);
    expect(screen.getByText("first-layer")).toBeInTheDocument();
    expect(screen.getByText("10 to 110 mm")).toBeInTheDocument();
    expect(screen.getByText("20 to 120 mm")).toBeInTheDocument();
    expect(screen.getByText("0.09 mm")).toBeInTheDocument();
  });

  it.each([
    ["unconfigured", "Klipper bed mesh is not configured."],
    ["inactive", "No active bed mesh."],
  ] as const)("reports a %s mesh without a grid", async (state, message) => {
    renderApp(<PrinterBedSurface printerId={8} />, {
      routes: { [bedMeshPath]: () => json({ state }) },
    });

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("recovers from a provider error with an explicit refresh", async () => {
    const app = renderApp(<PrinterBedSurface printerId={8} />, {
      routes: { [bedMeshPath]: () => json({ detail: "printer_inspection_unreachable" }, 502) },
    });

    expect(await screen.findByText("Could not read the bed mesh.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    app.route({ [bedMeshPath]: () => json(activeMesh) });

    const refresh = screen.getByRole("button", { name: "Refresh bed mesh" });
    refresh.focus();
    await userEvent.keyboard("{Enter}");

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(
      app.requestsWithMethod("GET").filter(({ url }) => url.endsWith("/bed-mesh")),
    ).toHaveLength(2);
  });

  it("shows a loading state while the controller is pending", () => {
    const pending = Promise.withResolvers<Response>();
    renderApp(<PrinterBedSurface printerId={8} />, {
      routes: { [bedMeshPath]: () => pending.promise },
    });

    expect(screen.getByText("Reading bed mesh…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh bed mesh" })).toBeDisabled();
  });

  it("offers Measure bed next to Refresh on an inactive supported printer", async () => {
    renderApp(<PrinterBedSurface printerId={8} control={readyControl()} />, {
      routes: automaticBed,
    });

    const section = within(screen.getByRole("region", { name: "Bed surface" }));
    expect(await section.findByText("No active bed mesh.")).toBeInTheDocument();
    expect(await section.findByRole("button", { name: "Measure bed" })).toBeEnabled();
    expect(section.getByRole("button", { name: "Refresh bed mesh" })).toBeEnabled();
  });

  it("asks the operator to home each axis before measuring", async () => {
    const control = readyControl();
    control.snapshot.toolhead = { homed_axes: "xy" };
    const app = renderApp(<PrinterBedSurface printerId={8} control={control} />, {
      routes: automaticBed,
    });

    expect(
      await screen.findByText("Home X, Y and Z before measuring the bed."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Measure bed" })).toBeDisabled();
    expect(app.requestsWithMethod("POST")).toEqual([]);
  });

  it("blocks measurement during another printer command", async () => {
    renderApp(<PrinterBedSurface printerId={8} control={readyControl({ busy: true })} />, {
      routes: automaticBed,
    });

    expect(await screen.findByRole("button", { name: "Measure bed" })).toBeDisabled();
  });

  it("asks the operator to wait until the printer is idle", async () => {
    const control = readyControl();
    control.snapshot.print_stats = { state: "paused" };
    renderApp(<PrinterBedSurface printerId={8} control={control} />, { routes: automaticBed });

    expect(
      await screen.findByText("Wait until the printer is idle to measure the bed."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Measure bed" })).toBeDisabled();
  });

  it.each([
    [
      "a VIEW grant",
      readyControl({ printer: aPrinter({ access: printerAccess({ can_control: false }) }) }),
    ],
    ["a non-Moonraker provider", readyControl({ printer: aPrinter({ provider: "bambu_lan" }) })],
    ["an offline printer", readyControl({ live: false })],
  ] as const)("omits measurement for %s", async (_case, control) => {
    const app = renderApp(<PrinterBedSurface printerId={8} control={control} />, {
      routes: automaticBed,
    });

    await screen.findByText("No active bed mesh.");
    expect(screen.queryByRole("button", { name: "Measure bed" })).not.toBeInTheDocument();
    expect(app.requestsWithMethod("GET").map(({ url }) => url)).toEqual([
      "/api/v1/printers/8/bed-mesh",
    ]);
  });

  it("omits measurement when an automatic probe is unavailable", async () => {
    const app = renderApp(<PrinterBedSurface printerId={8} control={readyControl()} />, {
      routes: { ...automaticBed, [measurementAvailability]: () => json({ supported: false }) },
    });

    await waitFor(() =>
      expect(app.requestsWithMethod("GET").map(({ url }) => url)).toContain(measurementUrl),
    );
    await screen.findByText("No active bed mesh.");
    expect(screen.queryByRole("button", { name: "Measure bed" })).not.toBeInTheDocument();
  });

  it("sends nothing when the operator cancels confirmation", async () => {
    const app = renderApp(<PrinterBedSurface printerId={8} control={readyControl()} />, {
      routes: automaticBed,
    });

    const dialog = await openMeasurement();
    expect(within(dialog).getByText(/Clear the bed/)).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("blocks a confirmation if the printer goes offline while the dialog is open", async () => {
    const app = renderApp(<PrinterBedSurface printerId={8} control={readyControl()} />, {
      routes: automaticBed,
    });

    await openMeasurement();
    app.rerender(<PrinterBedSurface printerId={8} control={readyControl({ live: false })} />);
    const dialog = screen.getByRole("dialog", { name: "Measure bed" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Measure bed" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("shows pending then the measured grid from the same-origin command", async () => {
    const receipt = Promise.withResolvers<Response>();
    const app = renderApp(<PrinterBedSurface printerId={8} control={readyControl()} />, {
      routes: { ...automaticBed, [measurementPost]: () => receipt.promise },
    });

    const section = within(screen.getByRole("region", { name: "Bed surface" }));
    const dialog = await openMeasurement();
    await userEvent.click(within(dialog).getByRole("button", { name: "Measure bed" }));

    expect(await screen.findByText("Measuring bed…")).toBeVisible();
    expect(section.getByRole("button", { name: "Refresh bed mesh" })).toBeDisabled();
    expect(section.getByRole("button", { name: "Measure bed" })).toBeDisabled();
    expect(app.requests()).toContainEqual({
      method: "POST",
      url: measurementUrl,
      body: '{"confirmed":true}',
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    receipt.resolve(json(activeMesh));
    expect(
      await screen.findByRole("table", { name: "Probe heights in millimetres" }),
    ).toBeVisible();
    expect(screen.getByText("0.09 mm")).toBeVisible();
    expect(section.getByText("Bed measured")).toBeVisible();
    expect(screen.queryByText("Measuring bed…")).not.toBeInTheDocument();
  });

  it.each([
    ["a device timeout", () => json({ detail: "bed_measurement_unknown" }, 502)],
    ["a proxy timeout", () => new Response("Gateway timed out", { status: 504 })],
  ])("requires refresh after %s without replaying the command", async (_case, fail) => {
    const app = renderApp(<PrinterBedSurface printerId={8} control={readyControl()} />, {
      routes: { ...automaticBed, [measurementPost]: fail },
    });

    const section = within(screen.getByRole("region", { name: "Bed surface" }));
    const dialog = await openMeasurement();
    await userEvent.click(within(dialog).getByRole("button", { name: "Measure bed" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The measurement outcome is unknown",
    );
    expect(section.getByRole("button", { name: "Measure bed" })).toBeDisabled();
    app.route({ [bedMeshPath]: () => json(activeMesh) });
    await userEvent.click(section.getByRole("button", { name: "Refresh bed mesh" }));

    expect(await screen.findByRole("table")).toBeVisible();
    await waitFor(() => expect(section.getByRole("button", { name: "Measure bed" })).toBeEnabled());
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
});
