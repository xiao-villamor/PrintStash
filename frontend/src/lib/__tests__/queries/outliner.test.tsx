/** Filter changes must cancel old sidebar requests before they can replace visible rows. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useOutlinerEntries, useOutlinerCollections, useOutlinerRestore } from "@/lib/queries";
import { retirePrivateSessionScope } from "@/lib/auth-store";
import { act } from "@testing-library/react";
import { queryKeys } from "@/lib/query-client";
import type { OutlinerCollectionPage, OutlinerRestoreRead } from "@/types/outliner";
import { aCollectionNode } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

describe("outliner query isolation", () => {
  it("isolates late responses from obsolete filters", async () => {
    const pending = Promise.withResolvers<Response>();
    let obsoleteSignal: AbortSignal | null | undefined;
    function Probe() {
      const [favorites, setFavorites] = useState(false);
      const query = useOutlinerEntries({ view: "all", favorites });
      return (
        <>
          <button onClick={() => setFavorites(true)}>Favorites only</button>
          <output>
            {query.data?.pages
              .flatMap((p) => p.items)
              .map((item) => item.name)
              .join(",")}
          </output>
        </>
      );
    }
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/outliner/entries": (url, init) => {
          if (new URL(url, "http://test").searchParams.get("favorites") === "true")
            return json({
              items: [
                {
                  kind: "model",
                  id: 2,
                  name: "Current favorite",
                  collection: null,
                  collection_id: null,
                  collection_label: null,
                },
              ],
              next_cursor: null,
            });
          obsoleteSignal = init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() => expect(obsoleteSignal).toBeDefined());
    await userEvent.click(screen.getByRole("button", { name: "Favorites only" }));
    await screen.findByText("Current favorite");
    expect(obsoleteSignal?.aborted).toBe(true);
    pending.resolve(
      json({ items: [{ kind: "model", id: 1, name: "Obsolete item" }], next_cursor: null }),
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Current favorite"));
    expect(screen.queryByText("Obsolete item")).not.toBeInTheDocument();
  });
});

function restoredPage(name: string, id = 2): OutlinerCollectionPage {
  return {
    items: [
      {
        ...aCollectionNode({ id, name }),
        direct_entry_count: 0,
        subtree_entry_count: 0,
        visible_child_count: 0,
      },
    ],
    next_cursor: null,
    parent_direct_entry_count: 0,
    revealed: null,
  };
}

function RestorationProbe({
  paths = ["parts"],
  parentId = 1,
  selectedPath = null,
}: {
  paths?: string[];
  parentId?: number;
  selectedPath?: string | null;
}) {
  const [favorites, setFavorites] = useState(false);
  const scope = { view: "all" as const, favorites: favorites || undefined };
  const restore = useOutlinerRestore({
    ...scope,
    expanded_paths: paths,
    selected_path: selectedPath,
  });
  const query = useOutlinerCollections({ ...scope, parent_id: parentId }, !restore.isPending);
  return (
    <>
      <button onClick={() => setFavorites(true)}>Favorites only</button>
      <output aria-label="Restored siblings">
        {query.data?.pages
          .flatMap((p) => p.items)
          .map((p) => p.name)
          .join(",")}
      </output>
    </>
  );
}

describe("useOutlinerRestore", () => {
  it("seeds the tree before descendant hooks fetch", async () => {
    const app = renderApp(<RestorationProbe />, {
      routes: {
        "POST /api/v1/outliner/restore": json({
          collections: [{ parent_id: 1, page: restoredPage("Restored child") }],
          entries: [],
        } satisfies OutlinerRestoreRead),
      },
    });

    expect(await screen.findByText("Restored child")).toBeVisible();
    expect(app.requests().map((r) => [r.method, r.url])).toEqual([
      ["POST", "/api/v1/outliner/restore"],
    ]);
  });

  it("reuses restoration after returning from a model", async () => {
    function NavigationProbe() {
      const [atModel, setAtModel] = useState(false);
      const [selectedPath, setSelectedPath] = useState<string | null>(null);
      return (
        <>
          <button
            onClick={() => {
              setSelectedPath("parts");
              setAtModel(true);
            }}
          >
            Open model
          </button>
          <button onClick={() => setAtModel(false)}>Back to library</button>
          {atModel ? <div>Model detail</div> : <RestorationProbe selectedPath={selectedPath} />}
        </>
      );
    }
    const app = renderApp(<NavigationProbe />, {
      routes: {
        "POST /api/v1/outliner/restore": json({
          collections: [{ parent_id: 1, page: restoredPage("Restored child") }],
          entries: [],
        } satisfies OutlinerRestoreRead),
      },
    });

    expect(await screen.findByText("Restored child")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Open model" }));
    expect(screen.queryByText("Restored child")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Back to library" }));
    expect(await screen.findByText("Restored child")).toBeVisible();
    expect(app.requests().map((r) => [r.method, r.url])).toEqual([
      ["POST", "/api/v1/outliner/restore"],
    ]);
  });

  it("preserves downloaded cursor pages during restoration", async () => {
    const first = { ...restoredPage("First"), next_cursor: "next" };
    const second = restoredPage("Second", 3);
    renderApp(<RestorationProbe />, {
      seed: [
        [
          queryKeys.outlinerCollections({ view: "all", parent_id: 1 }),
          { pages: [first, second], pageParams: [null, "next"] },
        ],
      ],
      routes: {
        "POST /api/v1/outliner/restore": json({
          collections: [{ parent_id: 1, page: restoredPage("Replacement") }],
          entries: [],
        } satisfies OutlinerRestoreRead),
      },
    });

    expect(await screen.findByText("First,Second")).toBeVisible();
    await waitFor(() => expect(screen.queryByText("Replacement")).not.toBeInTheDocument());
  });

  it("isolates obsolete restoration responses", async () => {
    const pending = Promise.withResolvers<Response>();
    let obsoleteSignal: AbortSignal | null | undefined;
    const app = renderApp(<RestorationProbe />, {
      routes: {
        "POST /api/v1/outliner/restore": (_url, init) => {
          if (String(init?.body).includes('"favorites":true'))
            return json({
              collections: [{ parent_id: 1, page: restoredPage("Favorite child") }],
              entries: [],
            } satisfies OutlinerRestoreRead);
          obsoleteSignal = init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() => expect(obsoleteSignal).toBeDefined());

    await userEvent.click(screen.getByRole("button", { name: "Favorites only" }));
    await screen.findByText("Favorite child");
    pending.resolve(
      json({
        collections: [{ parent_id: 1, page: restoredPage("Obsolete") }],
        entries: [],
      } satisfies OutlinerRestoreRead),
    );

    expect(obsoleteSignal?.aborted).toBe(true);
    await waitFor(() =>
      expect(
        app.client.getQueryData(queryKeys.outlinerCollections({ view: "all", parent_id: 1 })),
      ).toBeUndefined(),
    );
    expect(screen.queryByText("Obsolete")).not.toBeInTheDocument();
  });

  it("rejects a response from the retired session", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<RestorationProbe />, {
      routes: { "POST /api/v1/outliner/restore": () => pending.promise },
    });
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    app.unmount();
    act(() => retirePrivateSessionScope());
    await act(async () =>
      pending.resolve(
        json({
          collections: [{ parent_id: 1, page: restoredPage("Private old row") }],
          entries: [],
        } satisfies OutlinerRestoreRead),
      ),
    );
    expect(
      app.client.getQueryData(queryKeys.outlinerCollections({ view: "all", parent_id: 1 })),
    ).toBeUndefined();
  });

  it("falls back to paged reads when restoration fails", async () => {
    const app = renderApp(<RestorationProbe />, {
      routes: {
        "POST /api/v1/outliner/restore": json({ detail: "temporary" }, 503),
        "GET /api/v1/outliner/collections": json(restoredPage("Ordinary child")),
      },
    });

    expect(await screen.findByText("Ordinary child")).toBeVisible();
    expect(app.requests().map((r) => r.method)).toEqual(["POST", "GET"]);
  });

  it("restores all paths across bounded batches", async () => {
    const paths = Array.from({ length: 27 }, (_, i) => `folder-${i}`);
    const app = renderApp(<RestorationProbe paths={paths} parentId={27} />, {
      routes: {
        "POST /api/v1/outliner/restore": (_url, init) => {
          const body: { expanded_paths: string[] } = JSON.parse(String(init?.body));
          return json({
            collections: body.expanded_paths.includes("folder-26")
              ? [{ parent_id: 27, page: restoredPage("Final branch") }]
              : [{ parent_id: 1, page: restoredPage("First branch") }],
            entries: [],
          } satisfies OutlinerRestoreRead);
        },
      },
    });

    expect(await screen.findByText("Final branch")).toBeVisible();
    const bodies: { expanded_paths: string[] }[] = app
      .requestsWithMethod("POST")
      .map((r) => JSON.parse(r.body));
    expect(bodies.map((b) => b.expanded_paths.length)).toEqual([16, 11]);
    expect(bodies.flatMap((b) => b.expanded_paths)).toEqual(paths);
  });
});
