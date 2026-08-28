/*
 * The revision badge on a model card, when the user has renamed the revision.
 *
 * A card can show two different things about the same G-code: its *status*
 * (known-good, needs testing) and the user's own *label* ("0.2mm draft"). Those
 * are independent, and showing only one of them is the failure — a card that
 * displays the custom label alone hides that the revision was never verified,
 * which is exactly the information somebody about to print it needs.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelCard } from "@/components/model-card";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import type { ModelListItem, PrintSummaryRead } from "@/types";

const model: ModelListItem = {
  id: 1,
  name: "Cam Holder v4",
  slug: "cam-holder-v4",
  collection: null,
  collection_id: null,
  source_url: null,
  effective_role: "admin",
  tags: [],
  thumbnail_url: null,
  file_count: 2,
  mesh_file_id: null,
  printer_presence: [],
  updated_at: "2026-07-13T12:00:00Z",
  print_summary: null,
  recommended_revision_status: "needs_test",
  recommended_revision_label: "a",
  starred: false,
};

function printSummary(over: Partial<PrintSummaryRead> = {}): PrintSummaryRead {
  return {
    layer_height_mm: 0.2,
    estimated_time_s: 3600,
    filament_weight_g: 30,
    material_type: "PLA",
    slicer_name: "PrusaSlicer",
    ...over,
  };
}

/** The card in the app shell, which is what its star button talks through. */
function renderCard(over: Partial<ModelListItem> = {}, routes: RouteTable = {}) {
  return renderApp(<ModelCard model={{ ...model, ...over }} />, {
    routes: {
      "PUT /api/v1/models/1/star": json({ model_id: 1, starred: true }),
      "DELETE /api/v1/models/1/star": json({ model_id: 1, starred: false }),
      ...routes,
    },
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelCard", () => {
  it("shows revision status alongside a custom revision label", () => {
    // The card links to the model detail route and prefetches it on hover, so
    // it needs a real router; `thumbnail_url: null` keeps the thumbnail hook
    // from touching the network.
    render(
      <MemoryRouter>
        <ModelCard model={model} />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("Revision status: Needs Test; label: a")).toHaveTextContent(
      "Needs Test·a",
    );
  });
  describe("the metrics on the face of the card", () => {
    it("shows the layer height the slicer recorded", () => {
      // The grid is scanned, not read: these three numbers are what a user
      // compares between cards without opening either.
      renderCard({ print_summary: printSummary({ layer_height_mm: 0.2 }) });

      expect(screen.getByText("0.20 mm")).toBeInTheDocument();
    });

    it("shows a long print time in hours rather than seconds", () => {
      // Seconds are unreadable at a glance, which is the only way this is read.
      renderCard({ print_summary: printSummary({ estimated_time_s: 5400 }) });

      expect(screen.getByText("1h 30m")).toBeInTheDocument();
    });

    it("drops the hours from a print under an hour", () => {
      renderCard({ print_summary: printSummary({ estimated_time_s: 900 }) });

      expect(screen.getByText("15m")).toBeInTheDocument();
    });

    it("shows the filament weight", () => {
      renderCard({ print_summary: printSummary({ filament_weight_g: 42.4 }) });

      expect(screen.getByText("42 g")).toBeInTheDocument();
    });

    it("shows a dash for a figure the slicer never recorded", () => {
      // A blank cell reads as a rendering bug; a dash reads as "unknown", which
      // is what it is.
      renderCard({ print_summary: null });

      expect(screen.getAllByText("—")).not.toHaveLength(0);
    });

    it("shows the metrics the user chose instead of the defaults", () => {
      // The choice is per-browser, and the abbreviations are the only place it
      // shows — the default set has no slicer column at all.
      window.localStorage.setItem(
        "printstash.card.metrics",
        JSON.stringify(["material", "slicer", "file_count"]),
      );

      renderCard({ print_summary: printSummary({ material_type: "PETG" }) });

      expect(screen.getByText("SLR")).toBeInTheDocument();
    });
  });

  describe("favouriting from the grid", () => {
    it("stars the model", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();

      await user.click(screen.getByRole("button", { name: "Add Cam Holder v4 to favorites" }));

      await waitFor(() =>
        expect(requestsWithMethod("PUT").some((call) => call.url.endsWith("/star"))).toBe(true),
      );
    });

    it("lights the star before the server answers", async () => {
      // The grid is a fast, repetitive surface; waiting a round trip per click
      // makes starring ten models feel broken.
      const user = userEvent.setup();
      renderCard();

      await user.click(screen.getByRole("button", { name: "Add Cam Holder v4 to favorites" }));

      expect(
        await screen.findByRole("button", { name: "Remove Cam Holder v4 from favorites" }),
      ).toBeInTheDocument();
    });

    it("puts the star back when the server refuses", async () => {
      // A star left lit over a favourite that was never saved is a lie the user
      // only discovers on their next visit.
      const user = userEvent.setup();
      renderCard({}, { "PUT /api/v1/models/1/star": json({ detail: "forbidden" }, 403) });

      await user.click(screen.getByRole("button", { name: "Add Cam Holder v4 to favorites" }));

      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Add Cam Holder v4 to favorites" }),
        ).toBeInTheDocument(),
      );
    });

    it("unstars a model that was already a favourite", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({ starred: true });

      await user.click(screen.getByRole("button", { name: "Remove Cam Holder v4 from favorites" }));

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.endsWith("/star"))).toBe(true),
      );
    });
  });
});
