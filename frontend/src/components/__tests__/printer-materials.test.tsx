/*
 * What is loaded in a printer right now, and who says so.
 *
 * A slot's *source* is the whole design: a Bambu AMS reports its own slots, and
 * so does Moonraker-with-Spoolman. Those are observations, not settings, and
 * editing one here would produce a value the printer overwrites on its next
 * report — with the user never told why their change reverted. Only `manual`
 * slots are editable, and that boundary is what these tests hold.
 *
 * The save carries the timestamp the page loaded, so a printer that reported new
 * state in between is not silently overwritten by a form built before it. That
 * is the difference between "save my edit" and "discard whatever happened while
 * I had this open".
 *
 * Colour and material are what the fleet view matches a job against, so an empty
 * field has to mean "unknown" rather than an empty string that reads as a real
 * material named nothing.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrinterMaterials } from "@/components/printer-materials";
import { queryKeys } from "@/lib/query-client";
import { aPrinter } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { MaterialSlotRead, MaterialToolRead, PrinterMaterialStateRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aTool(over: Partial<MaterialToolRead> = {}): MaterialToolRead {
  return {
    tool_key: "tool0",
    label: "Tool 0",
    nozzle_diameter_mm: 0.4,
    source: "manual",
    observed_at: null,
    stale: false,
    ...over,
  };
}

function aSlot(over: Partial<MaterialSlotRead> = {}): MaterialSlotRead {
  return {
    slot_key: "slot0",
    label: "Slot 1",
    tool_key: "tool0",
    state: "loaded",
    source: "manual",
    confidence: "operator_set",
    material_type: "PLA",
    material_brand: "Prusament",
    color_hex: "#ff8800",
    spool_id: null,
    spool_name: null,
    spool_filament_id: null,
    observed_at: null,
    stale: false,
    ...over,
  };
}

function materialState(over: Partial<PrinterMaterialStateRead> = {}): PrinterMaterialStateRead {
  return {
    printer_id: 4,
    updated_at: FROZEN_NOW,
    provider_sync_enabled: false,
    tools: [aTool()],
    slots: [aSlot()],
    ...over,
  };
}

function renderMaterials(
  options: RenderAppOptions & { state?: PrinterMaterialStateRead; spoolman?: boolean } = {},
) {
  const { state = materialState(), spoolman = false, seed = [], routes = {}, ...rest } = options;
  return renderApp(<PrinterMaterials printer={aPrinter({ id: 4, name: "Voron" })} />, {
    seed: [
      [queryKeys.spoolmanStatus, { enabled: spoolman, url: null, reachable: spoolman }],
      [queryKeys.spools, []],
      ...seed,
    ],
    routes: {
      "GET /api/v1/printers/4/material-state": json(state),
      // Only the *manual* half is writable, and the path says so.
      "PUT /api/v1/printers/4/material-state/manual": json(state),
      "GET /api/v1/spoolman/status": json({ enabled: spoolman, url: null, reachable: spoolman }),
      "GET /api/v1/spoolman/spools": json([]),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrinterMaterials", () => {
  describe("what it shows", () => {
    it("lists the printer's slots", async () => {
      renderMaterials();

      expect(await screen.findByDisplayValue("Slot 1")).toBeInTheDocument();
    });

    it("shows the material loaded in a slot", async () => {
      renderMaterials();

      expect(await screen.findByDisplayValue("PLA")).toBeInTheDocument();
    });

    it("shows the tool's nozzle diameter", async () => {
      renderMaterials();

      expect(await screen.findByDisplayValue("0.4")).toBeInTheDocument();
    });
  });

  describe("who owns a slot", () => {
    it("offers no editing for a slot the printer reports itself", async () => {
      // Editing an AMS-reported slot produces a value the printer overwrites on
      // its next report, with the user never told why it reverted.
      renderMaterials({
        state: materialState({
          slots: [aSlot({ source: "bambu_ams", confidence: "provider_reported" })],
        }),
      });

      await waitFor(() => expect(screen.queryByDisplayValue("Slot 1")).toBeNull());
    });

    it("offers no editing for a slot Spoolman tracks", async () => {
      renderMaterials({
        state: materialState({
          slots: [aSlot({ source: "moonraker_spoolman", confidence: "externally_tracked" })],
        }),
      });

      await waitFor(() => expect(screen.queryByDisplayValue("Slot 1")).toBeNull());
    });
  });

  describe("saving what the operator set", () => {
    it("sends the material the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      const material = await screen.findByDisplayValue("PLA");

      await user.clear(material);
      await user.type(material, "PETG");
      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ material_type: "PETG" })],
        }),
      );
    });

    it("carries the state it was built from", async () => {
      // A printer that reported new state in between must not be silently
      // overwritten by a form built before it.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      await screen.findByDisplayValue("PLA");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          expected_updated_at: FROZEN_NOW,
        }),
      );
    });

    it("sends no material when the field is cleared", async () => {
      // An empty string reads downstream as a real material named nothing.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      const material = await screen.findByDisplayValue("PLA");

      await user.clear(material);
      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ material_type: null })],
        }),
      );
    });
  });

  describe("with Spoolman available", () => {
    it("offers the tracked spools", async () => {
      renderMaterials({
        spoolman: true,
        seed: [
          [
            queryKeys.spools,
            [{ id: 7, name: "PETG black", material: "PETG", remaining_weight: 800 }],
          ],
        ],
        routes: {
          "GET /api/v1/spoolman/spools": json([
            { id: 7, name: "PETG black", material: "PETG", remaining_weight: 800 },
          ]),
        },
      });

      await screen.findByDisplayValue("PLA");
      await waitFor(() => expect(screen.queryAllByText(/PETG black/).length).toBeGreaterThan(0));
    });
  });
  describe("adding a manual feed", () => {
    it("adds a slot the printer never reported", async () => {
      // A printer with no AMS reports no slots at all, so without this the
      // material fields are unreachable on exactly the machines that need them
      // typed in by hand.
      const user = userEvent.setup();
      renderMaterials({ state: materialState({ slots: [] }) });
      await screen.findByRole("button", { name: /Add manual feed|Add feed|Add/ });

      await user.click(screen.getByRole("button", { name: /Add manual feed|Add feed|Add/ }));

      expect(await screen.findByDisplayValue("Manual feed 1")).toBeInTheDocument();
    });

    it("numbers a second manual feed distinctly", async () => {
      // Two slots keyed the same would overwrite each other on save.
      const user = userEvent.setup();
      renderMaterials({ state: materialState({ slots: [] }) });
      const add = await screen.findByRole("button", { name: /Add manual feed|Add feed|Add/ });
      await user.click(add);

      await user.click(add);

      expect(await screen.findByDisplayValue("Manual feed 2")).toBeInTheDocument();
    });
  });

  describe("what a slot is doing", () => {
    it("records a slot the operator emptied", async () => {
      // "Empty" and "unknown" are different: one says the operator checked.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      await screen.findByDisplayValue("PLA");

      await user.selectOptions(screen.getAllByRole("combobox")[0], "empty");
      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ state: "empty" })],
        }),
      );
    });

    it("falls back to unknown for a state nobody ships", async () => {
      // The select only renders the states we know, so an unmatched value can
      // only come from a tampered DOM — and "unknown" is the honest answer.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      await screen.findByDisplayValue("PLA");
      const stateSelect = screen.getAllByRole("combobox")[0];
      const rogue = document.createElement("option");
      rogue.value = "melted";
      stateSelect.append(rogue);

      await user.selectOptions(stateSelect, "melted");
      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ state: "unknown" })],
        }),
      );
    });
  });
});
