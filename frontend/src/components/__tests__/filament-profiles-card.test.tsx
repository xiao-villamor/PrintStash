/*
 * The filament and printer presets: the numbers every print cost is computed
 * from.
 *
 * These rows save on blur rather than on a button, which is what makes them
 * quick to edit and also what makes them easy to get wrong. A row that saves the
 * value it started with, or saves on every keystroke, is invisible either way —
 * the user sees the field they typed in and no error. So the tests assert the
 * request each edit produces, and that leaving a row unchanged produces none.
 *
 * Cost per kg is the one field with consequences beyond this card: it multiplies
 * into every print's price, so an empty box has to mean "unknown" rather than
 * zero. A preset silently costed at zero makes a whole library's statistics
 * wrong in a direction nobody questions.
 *
 * Spoolman is the other axis. When it is the source of truth its presets are
 * read-only here, because a local edit would be overwritten on the next sync and
 * the user would have no way to tell why their change reverted.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FilamentProfilesCard } from "@/components/filament-profiles-card";
import { queryKeys } from "@/lib/query-client";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FilamentProfileRead, PrinterProfileRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aFilamentProfile(over: Partial<FilamentProfileRead> = {}): FilamentProfileRead {
  return {
    id: 1,
    name: "Everyday PLA",
    material_type: "PLA",
    material_brand: "Prusament",
    cost_per_kg: 24.5,
    notes: null,
    usage_count: 0,
    spoolman_filament_id: null,
    density_g_cm3: null,
    diameter_mm: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    ...over,
  };
}

function aPrinterProfile(over: Partial<PrinterProfileRead> = {}): PrinterProfileRead {
  return {
    id: 1,
    name: "Voron 2.4 — 0.4 mm",
    printer_model: "Voron 2.4",
    slicer_name: null,
    nozzle_diameter_mm: 0.4,
    notes: null,
    usage_count: 0,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    ...over,
  };
}

function renderCard(
  options: RenderAppOptions & {
    filaments?: FilamentProfileRead[];
    printers?: PrinterProfileRead[];
    spoolman?: boolean;
  } = {},
) {
  const {
    filaments = [aFilamentProfile()],
    printers = [aPrinterProfile()],
    spoolman = false,
    seed = [],
    routes = {},
    ...rest
  } = options;
  return renderApp(<FilamentProfilesCard />, {
    seed: [
      [queryKeys.spoolmanStatus, { enabled: spoolman, url: null, reachable: spoolman }],
      ...seed,
    ],
    routes: {
      "GET /api/v1/filament-profiles": json(filaments),
      "GET /api/v1/printer-profiles": json(printers),
      "GET /api/v1/spoolman/status": json({ enabled: spoolman, url: null, reachable: spoolman }),
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

describe("FilamentProfilesCard", () => {
  describe("listing presets", () => {
    it("lists the filament presets", async () => {
      renderCard();

      expect(await screen.findByLabelText("Filament preset name 1")).toHaveValue("Everyday PLA");
    });

    it("opens on the filament tab", async () => {
      renderCard();

      expect(await screen.findByText("Filament presets")).toBeInTheDocument();
    });

    it("shows the printer presets on their own tab", async () => {
      const user = userEvent.setup();
      renderCard();
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("tab", { name: /Printers/ }));

      expect(await screen.findByText("Printer presets")).toBeInTheDocument();
    });

    it("says so when the presets could not be loaded", async () => {
      renderCard({
        routes: { "GET /api/v1/filament-profiles": json({ detail: "unavailable" }, 503) },
      });

      expect(await screen.findByRole("alert")).toBeInTheDocument();
    });
  });

  describe("creating a filament preset", () => {
    async function openForm(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("button", { name: /New filament/ }));
    }

    it("refuses a preset with no name", async () => {
      const user = userEvent.setup();
      renderCard();

      await openForm(user);

      expect(screen.getByRole("button", { name: "Add preset" })).toBeDisabled();
    });

    it("POSTs the preset the user described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "POST /api/v1/filament-profiles": json(aFilamentProfile({ id: 2, name: "PETG" })),
        },
      });
      await openForm(user);
      await user.type(screen.getByPlaceholderText("Everyday PLA"), "PETG");
      await user.type(screen.getByPlaceholderText("PLA, PETG…"), "PETG");

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("filament-profiles"))
              ?.body ?? "{}",
          ),
        ).toMatchObject({ name: "PETG", material_type: "PETG" }),
      );
    });

    it("sends no cost when the field is left empty", async () => {
      // An empty box means "unknown", not zero. A preset silently costed at zero
      // makes every print using it look free.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "POST /api/v1/filament-profiles": json(aFilamentProfile({ id: 2, name: "PETG" })),
        },
      });
      await openForm(user);
      await user.type(screen.getByPlaceholderText("Everyday PLA"), "PETG");

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("filament-profiles"))
              ?.body ?? "{}",
          ),
        ).toMatchObject({ cost_per_kg: null }),
      );
    });
  });

  describe("editing a preset in place", () => {
    it("saves a changed field when the row is left", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "PATCH /api/v1/filament-profiles/1": json(aFilamentProfile({ name: "PETG" })) },
      });
      const name = await screen.findByLabelText("Filament preset name 1");

      await user.clear(name);
      await user.type(name, "PETG");
      // The row saves when focus leaves the *row*, not the field — tabbing to
      // the next input in the same row is still editing.
      await user.click(screen.getByText("Filament presets"));

      await waitFor(() =>
        expect(
          requestsWithMethod("PATCH").some((call) => call.url.includes("filament-profiles/1")),
        ).toBe(true),
      );
    });

    it("saves nothing when the row is left unchanged", async () => {
      // Blur fires on every pass through a row, so an unconditional save would
      // rewrite every preset a user merely scrolled past.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const name = await screen.findByLabelText("Filament preset name 1");

      await user.click(name);
      await user.click(screen.getByText("Filament presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("deletes a preset the user removes", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "DELETE /api/v1/filament-profiles/1": json(null, 204) },
      });
      await screen.findByLabelText("Filament preset name 1");

      await user.click(screen.getByRole("button", { name: /Delete filament preset/ }));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.includes("filament-profiles/1")),
        ).toBe(true),
      );
    });
  });

  describe("when Spoolman owns the presets", () => {
    it("marks a synced preset as such", async () => {
      renderCard({ spoolman: true, filaments: [aFilamentProfile({ spoolman_filament_id: 7 })] });

      expect(await screen.findByText("Synced")).toBeInTheDocument();
    });

    it("refuses to edit a synced preset locally", async () => {
      // A local edit would be overwritten on the next sync, with nothing said.
      renderCard({ spoolman: true, filaments: [aFilamentProfile({ spoolman_filament_id: 7 })] });

      expect(await screen.findByLabelText("Filament preset name 1")).toBeDisabled();
    });

    it("offers to pull the presets from Spoolman", async () => {
      renderCard({ spoolman: true });

      expect(await screen.findByRole("button", { name: /Sync|Import/ })).toBeInTheDocument();
    });

    it("offers no sync when Spoolman is not configured", async () => {
      renderCard();

      await screen.findByText("Filament presets");
      expect(screen.queryByRole("button", { name: /Sync from Spoolman/ })).toBeNull();
    });
  });

  describe("syncing from Spoolman", () => {
    it("offers no sync when Spoolman is not configured", async () => {
      // The button would 400; a control that only fails reads as the feature
      // being broken rather than absent.
      renderCard();

      await screen.findByText("Filament presets");
      expect(screen.queryByRole("button", { name: /Sync Spoolman/ })).toBeNull();
    });

    it("pulls the filaments Spoolman knows about", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({ created: 2, updated: 1, adopted: 0 }),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("sync-filaments"))).toBe(
          true,
        ),
      );
    });

    it("says what the sync changed", async () => {
      // "Synced" alone leaves the operator unsure whether anything moved.
      const user = userEvent.setup();
      renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({ created: 2, updated: 1, adopted: 0 }),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      expect(await screen.findByText(/2 added, 1 updated/)).toBeInTheDocument();
    });

    it("surfaces a Spoolman that could not be reached", async () => {
      const user = userEvent.setup();
      renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({ detail: "spoolman_unreachable" }, 502),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      expect(await screen.findByText("Spoolman unreachable.")).toBeInTheDocument();
    });
  });

  describe("removing a preset", () => {
    it("removes the filament preset the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "DELETE /api/v1/filament-profiles/1": json(null, 204) },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: "Delete filament preset Everyday PLA" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/filament-profiles/1")),
        ).toBe(true),
      );
    });

    it("removes the printer preset the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "DELETE /api/v1/printer-profiles/1": json(null, 204) },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));

      await user.click(
        screen.getByRole("button", { name: "Delete printer preset Voron 2.4 — 0.4 mm" }),
      );

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/printer-profiles/1")),
        ).toBe(true),
      );
    });

    it("surfaces a preset the server would not remove", async () => {
      // A preset in use by a revision cannot go; silently leaving it on screen
      // reads as the button not working.
      const user = userEvent.setup();
      renderCard({
        routes: {
          "DELETE /api/v1/filament-profiles/1": json({ detail: "profile_in_use" }, 409),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: "Delete filament preset Everyday PLA" }));

      expect(await screen.findByText("Profile in use.")).toBeInTheDocument();
    });
  });

  describe("creating a printer preset", () => {
    it("POSTs the preset the user described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "POST /api/v1/printer-profiles": json(aPrinterProfile({ id: 2, name: "Prusa MK4" })),
        },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      await user.click(screen.getByRole("button", { name: /New printer/ }));
      await user.type(screen.getByPlaceholderText("Voron 2.4 — 0.4 mm"), "Prusa MK4");

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("printer-profiles")),
        ).toBe(true),
      );
    });
  });
  describe("editing a printer preset in place", () => {
    it("saves a changed field when the row is left", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "PATCH /api/v1/printer-profiles/1": json(aPrinterProfile({ name: "MK4" })) },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByLabelText("Printer preset name 1");

      await user.clear(name);
      await user.type(name, "MK4");
      await user.click(screen.getByText("Printer presets"));

      await waitFor(() =>
        expect(
          requestsWithMethod("PATCH").some((call) => call.url.includes("printer-profiles/1")),
        ).toBe(true),
      );
    });

    it("saves nothing when the row is left unchanged", async () => {
      // Blur fires on every pass through a row, so an unconditional save would
      // rewrite every preset a user merely scrolled past.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByLabelText("Printer preset name 1");

      await user.click(name);
      await user.click(screen.getByText("Printer presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("refuses a nozzle diameter that is not a number", async () => {
      // It is written into every G-code compatibility check; a NaN there
      // silently matches nothing.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const nozzle = await screen.findByLabelText("Printer nozzle diameter 1");

      await user.clear(nozzle);
      await user.type(nozzle, "wide");
      await user.click(screen.getByText("Printer presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("surfaces a preset the server would not save", async () => {
      const user = userEvent.setup();
      renderCard({
        routes: { "PATCH /api/v1/printer-profiles/1": json({ detail: "name_taken" }, 409) },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByLabelText("Printer preset name 1");

      await user.clear(name);
      await user.type(name, "MK4");
      await user.click(screen.getByText("Printer presets"));

      expect(await screen.findByText("Name taken.")).toBeInTheDocument();
    });
  });

  describe("when a filament preset cannot be saved", () => {
    it("surfaces the server's refusal", async () => {
      const user = userEvent.setup();
      renderCard({
        routes: { "PATCH /api/v1/filament-profiles/1": json({ detail: "name_taken" }, 409) },
      });
      const name = await screen.findByLabelText("Filament preset name 1");

      await user.clear(name);
      await user.type(name, "PETG");
      await user.click(screen.getByText("Filament presets"));

      expect(await screen.findByText("Name taken.")).toBeInTheDocument();
    });

    it("refuses a cost that is not a number", async () => {
      // Cost feeds the statistics page; a NaN there poisons every total.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const cost = await screen.findByLabelText("Filament cost per kg 1");

      await user.clear(cost);
      await user.type(cost, "cheap");
      await user.click(screen.getByText("Filament presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });
  });
});
