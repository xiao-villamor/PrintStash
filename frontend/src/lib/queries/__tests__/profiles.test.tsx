/** A retired profiles acknowledgement cannot cancel or replace reads from the next private incarnation. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { json, renderApp } from "@/test-support/render";
import { FROZEN_NOW } from "@/test-support/factories";
import { useQuery } from "@tanstack/react-query";
import {
  useProfileCommands,
  filamentProfilesOptions,
  printerProfilesOptions,
} from "@/lib/queries/profiles";
import type { FilamentProfileRead, PrinterProfileRead } from "@/types";
const filament: FilamentProfileRead = {
  id: 1,
  name: "Earlier profile",
  material_type: "PLA",
  material_brand: "Generic",
  cost_per_kg: 24,
  notes: null,
  usage_count: 0,
  spoolman_filament_id: null,
  density_g_cm3: null,
  diameter_mm: null,
  created_at: FROZEN_NOW,
  updated_at: FROZEN_NOW,
};
const printer: PrinterProfileRead = {
  id: 1,
  name: "Earlier profile",
  printer_model: "Voron",
  slicer_name: null,
  nozzle_diameter_mm: 0.4,
  notes: null,
  usage_count: 0,
  created_at: FROZEN_NOW,
  updated_at: FROZEN_NOW,
};
const KINDS = [
  "createFilament",
  "updateFilament",
  "removeFilament",
  "createPrinter",
  "updatePrinter",
  "removePrinter",
  "syncSpoolman",
] as const;
type Kind = (typeof KINDS)[number];
const isPrinter = (kind: Kind) => kind.endsWith("Printer");
function Editor({ kind }: { kind: Kind }) {
  const filaments = useQuery({ ...filamentProfilesOptions(), enabled: !isPrinter(kind) });
  const printers = useQuery({ ...printerProfilesOptions(), enabled: isPrinter(kind) });
  const read = isPrinter(kind) ? printers : filaments;
  const commands = useProfileCommands();
  function save() {
    const session = getSessionVersion();
    switch (kind) {
      case "createFilament":
        commands.createFilament.mutate({ payload: { name: "Saved" }, session });
        break;
      case "updateFilament":
        commands.updateFilament.mutate({ id: 1, payload: { name: "Saved" }, session });
        break;
      case "removeFilament":
        commands.removeFilament.mutate({ id: 1, session });
        break;
      case "createPrinter":
        commands.createPrinter.mutate({ payload: { name: "Saved" }, session });
        break;
      case "updatePrinter":
        commands.updatePrinter.mutate({ id: 1, payload: { name: "Saved" }, session });
        break;
      case "removePrinter":
        commands.removePrinter.mutate({ id: 1, session });
        break;
      case "syncSpoolman":
        commands.syncSpoolman.mutate(session);
        break;
    }
  }
  return (
    <>
      <p>{read.data?.[0]?.name}</p>
      <button onClick={save}>Save</button>
    </>
  );
}
function renderCommand(kind: Kind) {
  return renderApp(<Editor kind={kind} />, {
    routes: {
      "GET /api/v1/filament-profiles": json([filament]),
      "GET /api/v1/printer-profiles": json([printer]),
      "POST /api/v1/filament-profiles": json({ ...filament, name: "Saved" }),
      "PATCH /api/v1/filament-profiles/1": json({ ...filament, name: "Saved" }),
      "DELETE /api/v1/filament-profiles/1": json(null, 204),
      "POST /api/v1/printer-profiles": json({ ...printer, name: "Saved" }),
      "PATCH /api/v1/printer-profiles/1": json({ ...printer, name: "Saved" }),
      "DELETE /api/v1/printer-profiles/1": json(null, 204),
      "POST /api/v1/spoolman/sync-filaments": json({
        created: 0,
        updated: 1,
        adopted: 0,
        unlinked: 0,
      }),
    },
  });
}

afterEach(() => vi.restoreAllMocks());
describe("retired profile acknowledgements", () => {
  it.each(KINDS.map((kind) => ({ kind })))(
    "leaves a new profile read active after retired $kind acknowledgement",
    async ({ kind }) => {
      const app = renderCommand(kind);
      await screen.findByText("Earlier profile");
      const cache = app.client.getMutationCache();
      const previousSuccess = cache.config.onSuccess;
      const previousSettled = cache.config.onSettled;
      const entered = Promise.withResolvers<void>();
      const resume = Promise.withResolvers<void>();
      const settled = Promise.withResolvers<void>();
      cache.config.onSuccess = async () => {
        entered.resolve();
        await resume.promise;
      };
      cache.config.onSettled = () => settled.resolve();
      const currentRead = Promise.withResolvers<Response>();
      let signal: AbortSignal | null | undefined;
      try {
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await entered.promise;
        app.unmount();
        clearLogin();
        renderApp(<Editor kind={kind} />, {
          routes: {
            [`GET /api/v1/${isPrinter(kind) ? "printer-profiles" : "filament-profiles"}`]: (
              _url,
              init,
            ) => {
              signal = init?.signal;
              return currentRead.promise;
            },
          },
        });
        await waitFor(() => expect(signal).toBeDefined());
        await act(async () => {
          resume.resolve();
          await settled.promise;
        });
        expect(signal?.aborted).toBe(false);
        await act(async () =>
          currentRead.resolve(
            json([{ ...(isPrinter(kind) ? printer : filament), name: "Current profile" }]),
          ),
        );
        expect(await screen.findByText("Current profile")).toBeVisible();
      } finally {
        cache.config.onSuccess = previousSuccess;
        cache.config.onSettled = previousSettled;
        resume.resolve();
        currentRead.resolve(
          json([{ ...(isPrinter(kind) ? printer : filament), name: "Current profile" }]),
        );
      }
    },
  );
  it("discards a profile acknowledgement after cancellation retires", async () => {
    const app = renderCommand("updateFilament");
    await screen.findByText("Earlier profile");
    const cache = app.client.getMutationCache();
    const previousSettled = cache.config.onSettled;
    const settled = Promise.withResolvers<void>();
    cache.config.onSettled = () => settled.resolve();
    const paused = Promise.withResolvers<void>();
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi.spyOn(app.client, "cancelQueries");
    spy.mockImplementationOnce(cancel).mockImplementationOnce(cancel);
    spy.mockImplementationOnce(() => paused.promise);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(4));
      await act(async () => {
        app.unmount();
        clearLogin();
        paused.resolve();
        await settled.promise;
      });
      expect(app.client.getQueriesData({ queryKey: ["filament-profiles"] })).toEqual([]);
    } finally {
      cache.config.onSettled = previousSettled;
      paused.resolve();
    }
  });
});
