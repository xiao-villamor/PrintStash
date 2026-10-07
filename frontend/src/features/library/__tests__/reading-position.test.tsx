/** A Library history entry owns its nested reading offsets for one session. */
import "@testing-library/jest-dom/vitest";
import { useRef, useState } from "react";
import { Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { json, renderApp } from "@/test-support/render";
import {
  acknowledgeFavoriteRemoval,
  useLibraryEntry,
  type LibraryLayout,
} from "../navigation-state";
import { getSessionVersion } from "@/lib/session-transport";
import { useLibraryReadingPosition } from "../reading-position";
import { useCollectionChildren } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { aCollectionNode } from "@/test-support/factories";

function Library({ layout }: { layout: LibraryLayout }) {
  const entry = useLibraryEntry("/?type=all", true);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useLibraryReadingPosition(entry, layout, main, list, true, {
    ready: true,
    models: { count: 1, more: false, pending: false, failed: false, next: () => {} },
    folders: { count: 1, more: false, pending: false, failed: false, next: () => {} },
  });
  return (
    <main ref={main} aria-label="Library">
      <div ref={list} role="region" aria-label="Rows">
        <Link to="/detail">Open Model</Link>
      </div>
    </main>
  );
}
function Detail() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>Back</button>;
}
function Browser({ layout = "grid" }: { layout?: LibraryLayout }) {
  return (
    <Routes>
      <Route path="/" element={<Library layout={layout} />} />
      <Route path="/detail" element={<Detail />} />
    </Routes>
  );
}

describe("Library reading position", () => {
  it.each(["grid", "list"] as const)("restores a cached %s entry's own containers", (layout) => {
    renderApp(<Browser layout={layout} />);
    const main = screen.getByRole("main");
    const list = screen.getByRole("region", { name: "Rows" });
    main.scrollTop = 240;
    list.scrollTop = 510;
    fireEvent.scroll(list);
    fireEvent.click(screen.getByRole("link", { name: "Open Model" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("main").scrollTop).toBe(240);
    expect(screen.getByRole("region", { name: "Rows" }).scrollTop).toBe(510);
  });

  it("rejects late scroll writes from a retired private view", () => {
    renderApp(<Browser />);
    const main = screen.getByRole("main");
    main.scrollTop = 240;
    fireEvent.scroll(main);
    act(() => clearLogin());
    main.scrollTop = 900;
    fireEvent.scroll(main);
    fireEvent.click(screen.getByRole("link", { name: "Open Model" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("main").scrollTop).toBe(0);
    expect(screen.getByRole("region", { name: "Rows" }).scrollTop).toBe(0);
  });
});

function FolderLibrary() {
  const query = useCollectionChildren(null);
  const ready = query.data !== undefined;
  const entry = useLibraryEntry("/?type=all", ready);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const { status } = useLibraryReadingPosition(entry, "grid", main, list, ready, {
    ready,
    models: { count: 1, more: false, pending: false, failed: false, next: () => {} },
    folders: {
      count: query.data?.pages.length ?? 0,
      more: query.hasNextPage ?? false,
      pending: query.isFetching,
      failed: query.isError,
      next: () => void query.fetchNextPage({ cancelRefetch: false }),
    },
  });
  return (
    <main ref={main}>
      <output>{status}</output>
      {query.data?.pages
        .flatMap((page) => page.items)
        .map((folder) => (
          <p key={folder.id}>{folder.name}</p>
        ))}
      <button disabled={!query.hasNextPage} onClick={() => void query.fetchNextPage()}>
        More folders
      </button>
      <Link to="/detail">Open Model</Link>
    </main>
  );
}

it.each([false, true])(
  "bounds folder reconstruction after eviction (failure=%s)",
  async (failed) => {
    let returning = false;
    const app = renderApp(
      <Routes>
        <Route path="/" element={<FolderLibrary />} />
        <Route path="/detail" element={<Detail />} />
      </Routes>,
      {
        routes: {
          "GET /api/v1/collections/children": (url) => {
            const cursor = new URL(url, "http://test").searchParams.get("cursor");
            if (returning && cursor && failed)
              return json({ detail: "browse_refresh_required" }, 409);
            return json({
              items: [
                aCollectionNode({
                  id: cursor ? 2 : 1,
                  name: cursor ? "Recovered folder" : "First folder",
                }),
              ],
              next_cursor: cursor ? "unvisited" : "second",
            });
          },
        },
      },
    );
    await screen.findByText("First folder");
    fireEvent.click(screen.getByRole("button", { name: "More folders" }));
    await screen.findByText("Recovered folder");
    fireEvent.click(screen.getByRole("link", { name: "Open Model" }));
    app.client.removeQueries({ queryKey: queryKeys.collectionChildren(null), exact: true });
    returning = true;
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    if (failed) await screen.findByText("reset");
    else await screen.findByText("Recovered folder");
    await waitFor(() => expect(app.client.isFetching()).toBe(0));
    const requests = app.requests().filter((request) => request.url.includes("cursor="));
    expect(requests).toHaveLength(2);
    expect(requests.every((request) => request.url.includes("cursor=second"))).toBe(true);
  },
);

function RefreshLibrary({ replace }: { replace: () => Promise<void> }) {
  const location = useLocation();
  const entry = useLibraryEntry(location.pathname, true);
  const [layout, setLayout] = useState<LibraryLayout>("grid");
  const [pages, setPages] = useState(2);
  const [failed, setFailed] = useState(false);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const reading = useLibraryReadingPosition(entry, layout, main, list, true, {
    ready: !failed,
    models: {
      count: pages,
      more: true,
      pending: false,
      failed: false,
      next: () => setPages((count) => count + 1),
    },
    folders: { count: 1, more: false, pending: false, failed: false, next: () => {} },
  });
  return (
    <main ref={main}>
      <output aria-label="Reading status">{reading.status}</output>
      <output aria-label="Loaded pages">{pages}</output>
      <button
        onClick={() =>
          void reading
            .refresh(async () => {
              setPages(1);
              await replace();
            })
            .catch(() => setFailed(true))
        }
      >
        Refresh
      </button>
      <button onClick={() => setLayout("list")}>List layout</button>
      <Link to="/another">Another Library</Link>
    </main>
  );
}

describe("Explicit Library refresh", () => {
  it.each([
    {
      label: "navigation retires its entry",
      retire: () => fireEvent.click(screen.getByRole("link", { name: "Another Library" })),
    },
    { label: "session retirement", retire: () => act(() => clearLogin()) },
    {
      label: "layout change",
      retire: () => fireEvent.click(screen.getByRole("button", { name: "List layout" })),
    },
  ])("ignores refresh restoration after $label", async ({ retire }) => {
    const response = Promise.withResolvers<void>();
    renderApp(<RefreshLibrary replace={() => response.promise} />);
    const main = screen.getByRole("main");
    main.scrollTop = 240;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByLabelText("Reading status")).toHaveTextContent("restoring");
    retire();
    main.scrollTop = 35;
    await act(async () => response.resolve());
    expect(screen.getByLabelText("Reading status")).toHaveTextContent("ready");
    expect(screen.getByLabelText("Loaded pages")).toHaveTextContent("1");
    expect(main.scrollTop).toBe(35);
  });

  it("ignores completion of a superseded refresh", async () => {
    const first = Promise.withResolvers<void>();
    const latest = Promise.withResolvers<void>();
    let next = first.promise;
    renderApp(<RefreshLibrary replace={() => next} />);
    const main = screen.getByRole("main");
    main.scrollTop = 240;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    main.scrollTop = 80;
    next = latest.promise;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    main.scrollTop = 0;
    await act(async () => first.resolve());
    expect(screen.getByLabelText("Reading status")).toHaveTextContent("restoring");
    expect(main.scrollTop).toBe(0);
    expect(screen.getByLabelText("Loaded pages")).toHaveTextContent("1");
    await act(async () => latest.resolve());
    expect(screen.getByLabelText("Reading status")).toHaveTextContent("ready");
    expect(main.scrollTop).toBe(80);
    expect(screen.getByLabelText("Loaded pages")).toHaveTextContent("1");
  });

  it("resets reading position when refresh replacement rejects", async () => {
    const response = Promise.withResolvers<void>();
    renderApp(<RefreshLibrary replace={() => response.promise} />);
    const main = screen.getByRole("main");
    main.scrollTop = 240;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await act(async () => response.reject(new Error("replacement unavailable")));
    expect(screen.getByLabelText("Reading status")).toHaveTextContent("reset");
    expect(main.scrollTop).toBe(0);
    expect(screen.getByLabelText("Loaded pages")).toHaveTextContent("1");
  });
});

function FavoriteRemoval({
  confirmed,
  displayed,
}: {
  confirmed: Promise<void>;
  displayed: Promise<void>;
}) {
  const entry = useLibraryEntry("/?favorites=true", true);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState("pending");
  const [removed, setRemoved] = useState(false);
  useLibraryReadingPosition(entry, "grid", main, list, true, {
    ready: true,
    models: { count: 1, more: false, pending: false, failed: false, next: () => {} },
    folders: { count: 1, more: false, pending: false, failed: false, next: () => {} },
  });
  async function remove() {
    const session = getSessionVersion();
    await confirmed;
    acknowledgeFavoriteRemoval("/models/2", session);
    setPhase("acknowledged");
    await displayed;
    setRemoved(true);
  }
  return (
    <main ref={main}>
      <output>{phase}</output>
      <article>
        <a href="/models/1" data-library-entry="/models/1">
          Earlier
        </a>
      </article>
      {!removed && (
        <article>
          <a href="/models/2" data-library-entry="/models/2">
            Removed
          </a>
          <button onClick={() => void remove()}>Unstar</button>
        </article>
      )}
      <article>
        <a href="/models/3" data-library-entry="/models/3">
          Neighbor
        </a>
      </article>
    </main>
  );
}

describe("Confirmed favorite reading position", () => {
  afterEach(() => vi.restoreAllMocks());
  it.each([
    { label: "active", retire: false, queuedScroll: false, expected: 0 },
    { label: "active with queued scroll", retire: false, queuedScroll: true, expected: 0 },
    { label: "retired", retire: true, queuedScroll: false, expected: 100 },
  ])(
    "applies the promoted anchor only for the $label session after DOM removal",
    async ({ retire, queuedScroll, expected }) => {
      const confirmed = Promise.withResolvers<void>();
      const displayed = Promise.withResolvers<void>();
      renderApp(<FavoriteRemoval confirmed={confirmed.promise} displayed={displayed.promise} />);
      const main = screen.getByRole("main");
      const earlier = screen.getByRole("link", { name: "Earlier" });
      const removed = screen.getByRole("link", { name: "Removed" });
      const neighbor = screen.getByRole("link", { name: "Neighbor" });
      vi.spyOn(main, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, 0, 100, 500));
      vi.spyOn(earlier, "getBoundingClientRect").mockImplementation(
        () => new DOMRect(0, 100 - main.scrollTop, 100, 100),
      );
      vi.spyOn(removed, "getBoundingClientRect").mockImplementation(
        () => new DOMRect(0, 200 - main.scrollTop, 100, 100),
      );
      vi.spyOn(neighbor, "getBoundingClientRect").mockImplementation(
        () => new DOMRect(0, (removed.isConnected ? 300 : 200) - main.scrollTop, 100, 100),
      );
      main.scrollTop = 100;
      fireEvent.scroll(main);
      fireEvent.click(screen.getByRole("button", { name: "Unstar" }));
      if (retire) act(() => clearLogin());
      await act(async () => confirmed.resolve());
      expect(screen.getByText("acknowledged")).toBeVisible();
      expect(removed).toBeInTheDocument();
      expect(main.scrollTop).toBe(100);
      if (queuedScroll) fireEvent.scroll(main);
      await act(async () => displayed.resolve());
      expect(screen.queryByRole("link", { name: "Removed" })).toBeNull();
      expect(main.scrollTop).toBe(expected);
      expect(neighbor.getBoundingClientRect().top).toBe(200 - expected);
    },
  );
});
