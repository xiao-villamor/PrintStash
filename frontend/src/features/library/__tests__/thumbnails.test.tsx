/** Thumbnail completion cannot replace the displayed browse snapshot. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLibraryThumbnails } from "../thumbnails";
import { libraryBrowseKeys, useLibraryBrowse } from "../browse";
import { clearLogin, getUser } from "@/lib/auth-store";
import { aModelListItem, aMultipartModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { LibraryBrowsePage } from "@/types/library-browse";

const params = { view: "all", sort: "name-asc", limit: 24 } as const;
const page: LibraryBrowsePage = {
  items: [
    { kind: "multipart", multipart: aMultipartModel({ name: "Zeta" }) },
    { kind: "model", model: aModelListItem({ id: 1, name: "Alpha", thumbnail_url: null }) },
  ],
  next_cursor: "next",
  total: 2,
  browse_revision: "r1",
  authorization_revision: "a1",
};
function Probe() {
  const browse = useLibraryBrowse(params);
  const first = browse.data?.pages[0] ?? null;
  const models = first?.items.flatMap((item) => (item.kind === "model" ? [item.model] : [])) ?? [];
  const thumbnails = useLibraryThumbnails(models, first, () => {});
  return (
    <>
      {!thumbnails.authorizationChanged && (
        <output>
          {first?.items
            .map((item) =>
              item.kind === "model"
                ? `${item.model.name}:${item.model.thumbnail_url}`
                : item.multipart.name,
            )
            .join(",")}
        </output>
      )}
      <button onClick={() => void thumbnails.refresh()}>Thumbnail arrived</button>
      {thumbnails.error && <p>Projection unavailable</p>}
    </>
  );
}

describe("useLibraryThumbnails", () => {
  it.each([403, 409])("retires a refused thumbnail projection (%s)", async (status) => {
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: {
        "GET /api/v1/models/browse/thumbnails": json(
          { detail: status === 403 ? "forbidden" : "browse_refresh_required" },
          status,
        ),
      },
    });
    await screen.findByText("Zeta,Alpha:null");
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    await waitFor(() => expect(screen.queryByText("Zeta,Alpha:null")).not.toBeInTheDocument());
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params))).toBeUndefined();
  });

  it("patches only the requested missing thumbnails", async () => {
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: {
        "GET /api/v1/models/browse/thumbnails": json({
          items: [{ model_id: 1, thumbnail_url: "/new.png" }],
          authorization_revision: "a1",
        }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    expect(await screen.findByText("Zeta,Alpha:/new.png")).toBeVisible();
    expect(app.requests().map((request) => new URL(request.url, "http://test").pathname)).toEqual([
      "/api/v1/models/browse/thumbnails",
    ]);
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params))).toMatchObject({
      pages: [{ next_cursor: "next", browse_revision: "r1", total: 2 }],
    });
  });

  it("preserves an already confirmed thumbnail", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: { "GET /api/v1/models/browse/thumbnails": () => pending.promise },
    });
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    act(() =>
      app.client.setQueryData(libraryBrowseKeys.pages(params), {
        pages: [
          {
            ...page,
            items: [
              {
                kind: "model",
                model: aModelListItem({
                  id: 1,
                  name: "Confirmed",
                  thumbnail_url: "/confirmed.png",
                }),
              },
            ],
          },
        ],
        pageParams: [null],
      }),
    );
    await screen.findByText("Confirmed:/confirmed.png");
    await act(async () =>
      pending.resolve(
        json({ items: [{ model_id: 1, thumbnail_url: "/old.png" }], authorization_revision: "a1" }),
      ),
    );
    expect(screen.getByText("Confirmed:/confirmed.png")).toBeVisible();
  });

  it("retires presentation when projection authority differs", async () => {
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: {
        "GET /api/v1/models/browse/thumbnails": json({ items: [], authorization_revision: "a2" }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    await waitFor(() => expect(screen.queryByText("Zeta,Alpha:null")).not.toBeInTheDocument());
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params))).toBeUndefined();
  });

  it("ignores a projection from a retired session", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: { "GET /api/v1/models/browse/thumbnails": () => pending.promise },
    });
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    act(() => clearLogin());
    await act(async () =>
      pending.resolve(
        json({
          items: [{ model_id: 1, thumbnail_url: "/private.png" }],
          authorization_revision: "a1",
        }),
      ),
    );
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params))).toBeUndefined();
    expect(screen.queryByText("Zeta,Alpha:/private.png")).not.toBeInTheDocument();
  });

  it("bounds a projection to 24 missing thumbnails", async () => {
    const many: LibraryBrowsePage = {
      ...page,
      items: Array.from({ length: 25 }, (_, i) => ({
        kind: "model",
        model: aModelListItem({ id: i + 1, thumbnail_url: null }),
      })),
    };
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [many], pageParams: [null] }]],
      routes: {
        "GET /api/v1/models/browse/thumbnails": json({ items: [], authorization_revision: "a1" }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    const ids = new URL(app.requests()[0].url, "http://test").searchParams.getAll("model_id");
    expect(ids).toEqual(Array.from({ length: 24 }, (_, i) => String(i + 1)));
  });

  it("rejects a malformed projection without revoking access", async () => {
    renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: { "GET /api/v1/models/browse/thumbnails": json({ items: [] }) },
    });
    await userEvent.click(screen.getByRole("button", { name: "Thumbnail arrived" }));
    expect(await screen.findByText("Projection unavailable")).toBeVisible();
    expect(screen.getByText("Zeta,Alpha:null")).toBeVisible();
    expect(getUser()?.id).toBe(1);
  });
});
