/** Collection mosaics read their previews after the page's own Model list. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  collectionPreviewItems,
  useCollectionOverview,
  type CollectionPreviewSource,
} from "../collection-overview";
import { aCollection, aModelListItem, aMultipartModel } from "@/test-support/factories";
import userEvent from "@testing-library/user-event";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
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

/** Recovery is visible through the same card surface, including retired sessions. */
function RecoverableMosaic({ retired, count = 2 }: { retired: () => void; count?: number }) {
  const { ref, pending, error, models, retry } = useCollectionOverview(
    aCollection({ model_count: count }),
    "a1",
    retired,
  );
  return (
    <div ref={ref}>
      <p>{pending ? "Loading previews" : error ? "Previews unavailable" : "Settled"}</p>
      {models?.map((model) => (
        <span key={model.key}>{model.thumbnail ?? "No thumbnail"}</span>
      ))}
      <button onClick={retry}>Retry previews</button>
    </div>
  );
}

const previewRoute = "GET /api/v1/models/browse/collection-preview";
const currentPreview = {
  items: [{ kind: "model", id: 1, thumbnail_url: "/private-cover.webp" }],
  authorization_revision: "a1",
};

describe("collection preview recovery", () => {
  it("retries a transient server error once", async () => {
    let reads = 0;
    renderApp(<RecoverableMosaic retired={() => {}} />, {
      routes: {
        [previewRoute]: () =>
          ++reads === 1 ? json({ detail: "unavailable" }, 503) : json(currentPreview),
      },
    });
    expect(await screen.findByText("/private-cover.webp", {}, { timeout: 2500 })).toBeVisible();
    expect(reads).toBe(2);
  });

  it("lets the user recover after a non-retriable missing preview", async () => {
    const user = userEvent.setup();
    const app = renderApp(<RecoverableMosaic retired={() => {}} />, {
      routes: { [previewRoute]: json({ detail: "not_found" }, 404) },
    });
    expect(await screen.findByText("Previews unavailable")).toBeVisible();
    expect(mosaicReads(app.requests)).toHaveLength(1);
    app.route({ [previewRoute]: json(currentPreview) });
    await user.click(screen.getByRole("button", { name: "Retry previews" }));
    expect(await screen.findByText("/private-cover.webp")).toBeVisible();
    expect(mosaicReads(app.requests)).toHaveLength(2);
  });

  it.each([401, 403])("retires private imagery after a %s response", async (status) => {
    const retired = vi.fn<() => void>();
    const app = renderApp(<RecoverableMosaic retired={retired} />, {
      routes: { [previewRoute]: json({ detail: "denied" }, status) },
    });
    const session = getSessionVersion();
    await waitFor(() => expect(getSessionVersion()).not.toBe(session));
    // HTTP 401 retires the whole session in transport; 403 retires this private scope.
    expect(retired).toHaveBeenCalledTimes(status === 401 ? 0 : 1);
    expect(screen.queryByText("/private-cover.webp")).not.toBeInTheDocument();
    expect(mosaicReads(app.requests)).toHaveLength(1);
  });

  it("retires a preview read from another authorization revision", async () => {
    const retired = vi.fn<() => void>();
    renderApp(<RecoverableMosaic retired={retired} />, {
      routes: { [previewRoute]: json({ ...currentPreview, authorization_revision: "a2" }) },
    });
    await waitFor(() => expect(retired).toHaveBeenCalledOnce());
    expect(screen.queryByText("/private-cover.webp")).not.toBeInTheDocument();
  });

  it("ignores retry after the signed-in session ends", async () => {
    const user = userEvent.setup();
    const app = renderApp(<RecoverableMosaic retired={() => {}} />, {
      routes: { [previewRoute]: json(currentPreview) },
    });
    await screen.findByText("/private-cover.webp");
    act(() => clearLogin());
    expect(screen.queryByText("/private-cover.webp")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry previews" }));
    expect(mosaicReads(app.requests)).toHaveLength(1);
  });

  it("does not fetch previews for an empty collection", async () => {
    const app = renderApp(<RecoverableMosaic retired={() => {}} count={0} />);
    expect(screen.getByText("Settled")).toBeVisible();
    expect(mosaicReads(app.requests)).toEqual([]);
  });
});

describe("collection mosaic reuse", () => {
  const collection = aCollection();
  function source(
    overrides: Partial<CollectionPreviewSource["params"]> = {},
  ): CollectionPreviewSource {
    return {
      params: { view: "all", sort: "date-desc", limit: 24, ...overrides },
      page: {
        items: [{ kind: "model", model: aModelListItem({ collection: "parts/child" }) }],
        next_cursor: null,
        total: 1,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    };
  }

  it("rejects a page belonging to a neighboring collection", () => {
    expect(
      collectionPreviewItems(collection, "a1", source({ collection: "parts-two" })),
    ).toBeNull();
  });

  it("rejects a parent prefix that is not a path segment", () => {
    expect(
      collectionPreviewItems(
        aCollection({ path: "parts-two" }),
        "a1",
        source({ collection: "parts" }),
      ),
    ).toBeNull();
  });

  it("accepts the complete descendant sequence from the same parent", () => {
    const page = source({ collection: "parts" });
    expect(collectionPreviewItems(collection, "a1", page)).toEqual(page.page.items);
  });

  it("fetches the dedicated preview when a collection has a custom cover", () => {
    expect(
      collectionPreviewItems(
        aCollection({ has_custom_cover: true }),
        "a1",
        source({ collection: "parts" }),
      ),
    ).toBeNull();
  });

  it("honors the server's recursive scope over the requested direct scope", () => {
    const page = source({ direct: true });
    page.page.effective_direct = false;
    expect(collectionPreviewItems(collection, "a1", page)).toEqual(page.page.items);
  });

  it("reuses mixed previews without reading unassigned Models", () => {
    const page = source();
    page.page.items.push(
      { kind: "model", model: aModelListItem({ id: 2, collection: null }) },
      {
        kind: "multipart",
        multipart: aMultipartModel({
          collection: collection.path,
          cover_thumbnail_url: "/multipart-cover.webp",
        }),
      },
    );
    const app = renderApp(<Mosaic afterPrimary source={page} />);
    expect(screen.getByText("2 previews")).toBeVisible();
    expect(mosaicReads(app.requests)).toEqual([]);
  });
});
