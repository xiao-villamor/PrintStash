/** Collection mosaics read their previews after the page's own Model list. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCollectionOverview, type CollectionPreviewSource } from "../collection-overview";
import { aCollection, aModelListItem } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

function Mosaic({
  afterPrimary,
  source,
}: {
  afterPrimary: boolean;
  source?: CollectionPreviewSource;
}) {
  const { ref, models } = useCollectionOverview(
    aCollection(),
    "a1",
    () => {},
    afterPrimary,
    source,
  );
  return <div ref={ref}>{models ? `${models.length} previews` : "no previews"}</div>;
}

const previews = json({
  items: [],
  total: 0,
  next_cursor: null,
  browse_revision: "r1",
  authorization_revision: "a1",
});
const mosaicReads = (requests: () => { url: string }[]) =>
  requests().filter((request) => request.url.startsWith("/api/v1/models/browse"));

beforeEach(() => {
  // No viewport gate in jsdom: the card counts as in view at once.
  vi.stubGlobal("IntersectionObserver", undefined);
});
afterEach(() => vi.unstubAllGlobals());

describe("useCollectionOverview", () => {
  it("retains the displayed mosaic while primary results refresh", async () => {
    const collection = aCollection();
    const source: CollectionPreviewSource = {
      params: { view: "all", sort: "date-desc", limit: 4 },
      page: {
        items: [{ kind: "model", model: aModelListItem({ collection: collection.path }) }],
        next_cursor: null,
        total: 1,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    };
    const app = renderApp(<Mosaic afterPrimary source={source} />);
    expect(screen.getByText("1 previews")).toBeVisible();
    app.rerender(<Mosaic afterPrimary={false} source={source} />);
    expect(screen.getByText("1 previews")).toBeVisible();
    expect(mosaicReads(app.requests)).toEqual([]);
  });

  it("does not read a mosaic before the page's own list has arrived", async () => {
    const app = renderApp(<Mosaic afterPrimary={false} />, {
      routes: { "GET /api/v1/models/browse": previews },
    });
    expect(screen.getByText("no previews")).toBeVisible();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(mosaicReads(app.requests)).toEqual([]);
  });

  it("reads the mosaic once the page's own list has arrived", async () => {
    const app = renderApp(<Mosaic afterPrimary={false} />, {
      routes: { "GET /api/v1/models/browse": previews },
    });
    app.rerender(<Mosaic afterPrimary />);
    await waitFor(() => expect(mosaicReads(app.requests)).toHaveLength(1));
    expect(await screen.findByText("0 previews")).toBeVisible();
  });
});
