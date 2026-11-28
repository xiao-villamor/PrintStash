/** Controller history must preserve missing samples and yield to newer live heater readings for the same printer. */
import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, renderApp } from "@/test-support/render";
import type { PrinterRead } from "@/types";

import {
  prependRecordedHeaters,
  usePrinterHeaterHistory,
  type FetchedHeaterHistory,
} from "../inspection";
import { emptyHeaterHistory, heaterSegments, HEATER_SAMPLE_LIMIT } from "../telemetry";

const recorded: FetchedHeaterHistory = {
  startedAt: 9900,
  receivedAt: 10_000,
  samples: {
    sample_interval_ms: 1000,
    extruder: [
      { age_ms: 4000, temperature: 0, target: null },
      { age_ms: 3000, temperature: null, target: 220 },
      { age_ms: 2000, temperature: 24, target: 220 },
      { age_ms: 1000, temperature: 26, target: 220 },
      { age_ms: 0, temperature: 30, target: 220 },
    ],
    heater_bed: [{ age_ms: 0, temperature: 22, target: 0 }],
  },
};

const access: PrinterRead["access"] = {
  role: "view",
  can_view: true,
  can_admin: false,
  can_control: false,
  can_print: false,
};

function HeaterTimeline({
  id,
  provider = "moonraker",
  status = "ready",
}: {
  id: number;
  provider?: PrinterRead["provider"];
  status?: PrinterRead["status"];
}) {
  const { history } = usePrinterHeaterHistory({ id, provider, access, status }, emptyHeaterHistory);
  return <output>{history.extruder.at(-1)?.degrees ?? "Empty"}</output>;
}

describe("recorded heater history", () => {
  it("keeps live readings through request overlap", () => {
    const live = {
      ...emptyHeaterHistory,
      extruder: [
        { at: 10_000, degrees: 35, connection: 0 },
        { at: 11_000, degrees: 38, connection: 1 },
      ],
    };
    const merged = prependRecordedHeaters(live, recorded);
    expect(merged.history.extruder).toEqual([
      { at: 6000, degrees: 0, connection: -1 },
      { at: 8000, degrees: 24, connection: -2 },
      ...live.extruder,
    ]);
    expect(merged.history.heater_bed.at(-1)?.degrees).toBe(22);
    expect(heaterSegments(merged.history.extruder)).toHaveLength(4);
    expect(merged.recorded).toBe(true);
  });

  it("leaves gaps where the store had no measurement", () => {
    const { history } = prependRecordedHeaters(emptyHeaterHistory, recorded);
    expect(
      heaterSegments(history.extruder).map((segment) => segment.map((sample) => sample.at)),
    ).toEqual([[6000], [8000, 9000, 10_000]]);
  });

  it("keeps the dense live series within its point limit", () => {
    const extruder = Array.from({ length: HEATER_SAMPLE_LIMIT }, (_, at) => ({
      at: at + 10_000,
      degrees: 22,
      connection: 1,
    }));
    const live = { ...emptyHeaterHistory, extruder };
    const { history } = prependRecordedHeaters(live, recorded);
    expect(history.extruder).toBe(extruder);
  });

  it("expires recorded samples relative to new observations", () => {
    const extruder = [{ at: 400_000, degrees: 30, connection: 0 }];
    const { history } = prependRecordedHeaters({ ...emptyHeaterHistory, extruder }, recorded);
    expect(history.extruder).toBe(extruder);
  });

  it("ignores a previous printer's result after navigation", async () => {
    const slow = Promise.withResolvers<Response>();
    const app = renderApp(<HeaterTimeline id={1} />, {
      routes: {
        "GET /api/v1/printers/1/heater-history": () => slow.promise,
        "GET /api/v1/printers/2/heater-history": () =>
          json({
            ...recorded.samples,
            extruder: [{ age_ms: 0, temperature: 205, target: 210 }],
          }),
      },
    });
    app.rerender(<HeaterTimeline id={2} />);
    expect(await screen.findByText("205")).toBeInTheDocument();

    await act(async () => slow.resolve(json(recorded.samples)));

    expect(screen.getByRole("status")).toHaveTextContent("205");
    expect(
      app.requestsWithMethod("GET").filter(({ url }) => url.endsWith("/heater-history")),
    ).toHaveLength(2);
  });

  it("does not ask a Bambu printer for Moonraker history", () => {
    const app = renderApp(<HeaterTimeline id={1} provider="bambu_lan" />);
    expect(screen.getByRole("status")).toHaveTextContent("Empty");
    expect(app.requests().some(({ url }) => url.endsWith("/heater-history"))).toBe(false);
  });

  it("does not ask an offline printer for its history", () => {
    const app = renderApp(<HeaterTimeline id={1} status="offline" />);
    expect(screen.getByRole("status")).toHaveTextContent("Empty");
    expect(app.requests().some(({ url }) => url.endsWith("/heater-history"))).toBe(false);
  });
});
