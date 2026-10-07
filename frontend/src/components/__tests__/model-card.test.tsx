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
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelCard } from "@/components/model-card";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import type { ModelListItem, PrintSummaryRead } from "@/types";

const model: ModelListItem = {
  edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  edit_version: 1,
  id: 1,
  name: "Cam Holder v4",
  slug: "cam-holder-v4",
  collection: null,
  collection_id: null,
  collection_label: null,
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

function CardLocation() {
  const location = useLocation();
  return <output aria-label="Current card location">{location.pathname}</output>;
}

describe("ModelCard", () => {
  it("opens the Model detail from its card", async () => {
    const user = userEvent.setup();
    renderApp(
      <>
        <ModelCard model={model} />
        <CardLocation />
      </>,
    );
    const link = screen.getByRole("link", { name: /Cam Holder v4/ });
    expect(link).toHaveAttribute("href", "/models/1");

    await user.click(link);

    expect(screen.getByLabelText("Current card location")).toHaveTextContent("/models/1");
  });

  it("does not convert on hover", async () => {
    const user = userEvent.setup();
    const { requests } = renderCard({ mesh_file_id: 7 });

    await user.hover(screen.getByText("Cam Holder v4"));

    expect(requests().filter((request) => request.url.includes("/stl"))).toEqual([]);
  });
  describe("collection badge", () => {
    it("shows the exact folder name instead of its slug", () => {
      renderApp(
        <ModelCard
          model={{ ...model, collection: "testing/my-parts" }}
          collectionLabel="Testing/My Parts"
        />,
      );

      expect(screen.getByText("My Parts")).toBeVisible();
      expect(screen.queryByText("my-parts")).toBeNull();
    });

    it("shows only the collection name from a hierarchy path", () => {
      renderApp(
        <ModelCard
          model={{ ...model, collection: "printstash-data/workstation/drawer-organizer" }}
          collectionLabel="PrintStash Data/Workstation/Drawer Organizer"
        />,
      );

      expect(screen.getByText("Drawer Organizer")).toBeVisible();
      expect(screen.queryByText("PrintStash Data/Workstation/Drawer Organizer")).toBeNull();
    });
  });

  describe("quick tag access", () => {
    it("offers adding tags on an editable untagged card", () => {
      renderApp(<ModelCard model={model} onEditTags={vi.fn<(item: ModelListItem) => void>()} />);

      expect(screen.getByRole("button", { name: "Add tags to Cam Holder v4" })).toBeVisible();
    });

    it("offers editing tags when the card already has one", () => {
      renderApp(
        <ModelCard
          model={{ ...model, tags: ["Workshop"] }}
          onEditTags={vi.fn<(item: ModelListItem) => void>()}
        />,
      );

      expect(screen.getByRole("button", { name: "Edit tags for Cam Holder v4" })).toBeVisible();
    });

    it("hides tag editing from a view-only card", () => {
      renderApp(
        <ModelCard
          model={{ ...model, effective_role: "view" }}
          onEditTags={vi.fn<(item: ModelListItem) => void>()}
        />,
      );

      expect(screen.queryByRole("button", { name: /tags (to|for) Cam Holder v4/ })).toBeNull();
    });
  });

  it("shows revision status alongside a custom revision label", () => {
    // The card links to the model detail route, so it needs a real router.
    // `thumbnail_url: null` keeps the thumbnail hook from touching the network.
    renderApp(<ModelCard model={model} />);

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
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      renderCard({}, { "PUT /api/v1/models/1/star": () => pending.promise });

      await user.click(screen.getByRole("button", { name: "Add Cam Holder v4 to favorites" }));

      expect(
        screen.getByRole("button", { name: "Remove Cam Holder v4 from favorites" }),
      ).toBeDisabled();
      await act(async () => pending.resolve(json({ model_id: 1, starred: true })));
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

describe("localized model card", () => {
  it("localizes card metric presentation", () => {
    localStorage.setItem("printstash.locale", "es");
    localStorage.setItem(
      "printstash.card.metrics",
      JSON.stringify(["layer_height", "material", "file_count"]),
    );
    renderApp(<ModelCard model={{ ...model, name: "Files", print_summary: printSummary() }} />, {
      locale: "es",
    });
    expect(screen.getByText("Files", { exact: true })).toBeVisible();
    expect(screen.getByText("CAPA", { exact: true })).toBeVisible();
    expect(screen.getByText("ARCH", { exact: true })).toBeVisible();
    expect(screen.getByText("0,20 mm", { exact: true })).toBeVisible();
    expect(screen.getByText("2 archivos", { exact: true })).toBeVisible();
  });
  it("localizes card actions while preserving the model name", () => {
    localStorage.setItem("printstash.locale", "es");
    renderApp(
      <ModelCard
        model={{ ...model, name: "Files" }}
        onEditTags={vi.fn<(item: ModelListItem) => void>()}
      />,
      { locale: "es" },
    );
    expect(screen.getByRole("button", { name: "Añadir Files a favoritos" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Añadir etiquetas a Files" })).toBeVisible();
  });
});

describe("similarity badge", () => {
  it("shows only the count provided for this Model", () => {
    renderCard({ similarity: { open_candidates: 3, confirmed: 2 } });
    expect(screen.getByText("3 similar to review")).toBeVisible();
  });
  it("omits an empty candidate badge", () => {
    renderCard({ similarity: { open_candidates: 0, confirmed: 2 } });
    expect(screen.queryByText(/similar to review/)).not.toBeInTheDocument();
  });
});
