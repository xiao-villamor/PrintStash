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
import { act, screen, waitFor, within } from "@testing-library/react";
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

async function editFeed() {
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  return screen.findByRole("textbox", { name: "Material type" });
}

function saveFeed() {
  return userEvent.click(
    within(screen.getByRole("dialog", { name: /manual feed/i })).getByRole("button", {
      name: "Save state",
    }),
  );
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

      expect(await screen.findByRole("heading", { name: "Slot 1" })).toBeInTheDocument();
    });

    it("shows the material loaded in a slot", async () => {
      renderMaterials();

      expect(await screen.findByText(/Manual feed · Loaded · PLA/)).toBeInTheDocument();
    });

    it("shows the tool's nozzle diameter", async () => {
      renderMaterials();

      expect(await screen.findByDisplayValue("0.4")).toBeInTheDocument();
    });
  });

  describe("manual feed dialogs", () => {
    it("discards canceled material edits", async () => {
      const view = renderMaterials();
      const material = await editFeed();
      await userEvent.clear(material);
      await userEvent.type(material, "PETG");
      await userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
      );
      expect(await screen.findByText(/Manual feed · Loaded · PLA/)).toBeVisible();
      expect(view.requestsWithMethod("PUT")).toHaveLength(0);
    });
    it("keeps an unrecorded color null when saving another field", async () => {
      const view = renderMaterials({
        state: materialState({ slots: [aSlot({ color_hex: null })] }),
      });
      const material = await editFeed();
      await userEvent.clear(material);
      await userEvent.type(material, "PETG");
      await saveFeed();
      await waitFor(() =>
        expect(JSON.parse(view.requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ color_hex: null, material_type: "PETG" })],
        }),
      );
    });
    it("waits for explicit loaded-feed confirmation", async () => {
      const view = renderMaterials({
        state: materialState({ slots: [aSlot({ state: "unknown" })] }),
      });
      await userEvent.click(await screen.findByRole("button", { name: "Set as loaded" }));
      expect(view.requestsWithMethod("PUT")).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Confirm loaded feed" }));
      await waitFor(() =>
        expect(JSON.parse(view.requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ state: "loaded" })],
        }),
      );
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

      await screen.findByRole("heading", { name: "Slot 1" });
      expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    });

    it("offers no editing for a slot Spoolman tracks", async () => {
      renderMaterials({
        state: materialState({
          slots: [aSlot({ source: "moonraker_spoolman", confidence: "externally_tracked" })],
        }),
      });

      await screen.findByRole("heading", { name: "Slot 1" });
      expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    });
  });

  describe("saving what the operator set", () => {
    it("sends the material the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      const material = await editFeed();

      await user.clear(material);
      await user.type(material, "PETG");
      await saveFeed();

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
      await screen.findByRole("heading", { name: "Slot 1" });

      await user.click(screen.getByRole("button", { name: "Save state" }));

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
      const material = await editFeed();

      await user.clear(material);
      await saveFeed();

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

      await editFeed();
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
      await saveFeed();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await user.click(add);

      expect(await screen.findByDisplayValue("Manual feed 2")).toBeInTheDocument();
    });
  });

  describe("what a slot is doing", () => {
    it("records a slot the operator emptied", async () => {
      // "Empty" and "unknown" are different: one says the operator checked.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderMaterials();
      await editFeed();

      await user.selectOptions(screen.getAllByRole("combobox")[0], "empty");
      await saveFeed();

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
      await editFeed();
      const stateSelect = screen.getAllByRole("combobox")[0];
      const rogue = document.createElement("option");
      rogue.value = "melted";
      stateSelect.append(rogue);

      await user.selectOptions(stateSelect, "melted");
      await saveFeed();

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          slots: [expect.objectContaining({ state: "unknown" })],
        }),
      );
    });
  });
});

describe("Material entry ownership", () => {
  it("preserves manual material drafts after conflict", async () => {
    const view = renderMaterials({
      routes: {
        "PUT /api/v1/printers/4/material-state/manual": json(
          { detail: "material_state_changed" },
          409,
        ),
      },
    });
    const material = await editFeed();
    await userEvent.clear(material);
    await userEvent.type(material, "PETG");
    await saveFeed();
    expect(await screen.findByRole("button", { name: "Review current values" })).toBeVisible();
    expect(material).toHaveValue("PETG");
    expect(
      view.requestsWithMethod("GET").filter((r) => r.url.includes("material-state")),
    ).toHaveLength(1);
    expect(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Save state" }),
    ).toBeDisabled();
  });
  it("retires material reads when changing printer", async () => {
    const deferred = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const view = renderMaterials({
      routes: {
        "GET /api/v1/printers/4/material-state": (_url, init) => {
          signal = init?.signal;
          return deferred.promise;
        },
        "GET /api/v1/printers/5/material-state": json(
          materialState({ printer_id: 5, slots: [aSlot({ material_type: "ABS" })] }),
        ),
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    view.rerender(<PrinterMaterials printer={aPrinter({ id: 5 })} />);
    expect(await screen.findByText(/Manual feed · Loaded · ABS/)).toBeVisible();
    await act(async () => deferred.resolve(json(materialState())));
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText(/Manual feed · Loaded · PLA/)).not.toBeInTheDocument();
  });
  it("separates material read failure from loading", async () => {
    const view = renderMaterials({
      routes: { "GET /api/v1/printers/4/material-state": json({ detail: "unavailable" }, 503) },
    });
    const retry = await screen.findByRole("button", { name: "Retry" });
    view.route({ "GET /api/v1/printers/4/material-state": json(materialState()) });
    await userEvent.click(retry);
    expect(await screen.findByText(/Manual feed · Loaded · PLA/)).toBeVisible();
  });
  it("adopts reviewed material state explicitly", async () => {
    const view = renderMaterials({
      routes: {
        "PUT /api/v1/printers/4/material-state/manual": json(
          { detail: "material_state_changed" },
          409,
        ),
      },
    });
    const material = await editFeed();
    await userEvent.clear(material);
    await userEvent.type(material, "PETG");
    await saveFeed();
    view.route({
      "GET /api/v1/printers/4/material-state": json(
        materialState({
          updated_at: "2026-01-02T00:00:00Z",
          slots: [aSlot({ material_type: "ABS" })],
        }),
      ),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review current values" }));
    expect(material).toHaveValue("PETG");
    await userEvent.click(await screen.findByRole("button", { name: "Use current values" }));
    expect(await screen.findByText(/Manual feed · Loaded · ABS/)).toBeVisible();
    expect(view.requestsWithMethod("PUT")).toHaveLength(1);
  });
  it("preserves manual draft during provider refresh", async () => {
    const view = renderMaterials();
    const material = await editFeed();
    await userEvent.clear(material);
    await userEvent.type(material, "PETG");
    view.route({
      "GET /api/v1/printers/4/material-state": json(
        materialState({
          updated_at: "2026-01-02T00:00:00Z",
          slots: [aSlot(), aSlot({ source: "bambu_ams", label: "Updated AMS" })],
        }),
      ),
    });
    await act(async () => {
      await view.client.invalidateQueries();
    });
    expect(material).toHaveValue("PETG");
    expect(await screen.findByText("Updated AMS")).toBeVisible();
    await saveFeed();
    expect(
      JSON.parse(view.requestsWithMethod("PUT").at(-1)?.body ?? "{}").expected_updated_at,
    ).toBe(FROZEN_NOW);
  });
  it("retires material save feedback on unmount", async () => {
    const deferred = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const view = renderMaterials({
      routes: {
        "PUT /api/v1/printers/4/material-state/manual": (_url, init) => {
          signal = init?.signal;
          return deferred.promise;
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Save state" }));
    view.unmount();
    await act(async () => deferred.resolve(json(materialState())));
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText("Materials and tools saved")).not.toBeInTheDocument();
  });
});
