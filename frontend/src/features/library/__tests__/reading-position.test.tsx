/** A Library history entry owns its nested reading offsets for one session. */
import "@testing-library/jest-dom/vitest";
import { useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "@tanstack/react-router";
import { Link } from "@/lib/link";
import { useRouter } from "@/lib/navigation";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
        <Link href="/detail">Open Model</Link>
      </div>
    </main>
  );
}
function Detail() {
  const navigate = useRouter();
  return <button onClick={() => navigate.back()}>Back</button>;
}
function Browser({ layout = "grid" }: { layout?: LibraryLayout }) {
  const location = useLocation();
  return location.pathname === "/detail" ? <Detail /> : <Library layout={layout} />;
}
function FolderBrowser() {
  const location = useLocation();
  return location.pathname === "/detail" ? <Detail /> : <FolderLibrary />;
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

  it.each(["grid", "list"] as const)(
    "captures %s offsets when leaving before scroll notification",
    (layout) => {
      renderApp(
        <>
          <Link href="/detail">Other page</Link>
          <Browser layout={layout} />
        </>,
      );
      const main = screen.getByRole("main");
      const list = screen.getByRole("region", { name: "Rows" });
      main.scrollTop = 240;
      list.scrollTop = 510;
      // Navigation can retire the view before its queued scroll notification.
      fireEvent.click(screen.getByRole("link", { name: "Other page" }));
      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      expect(screen.getByRole("main").scrollTop).toBe(240);
      expect(screen.getByRole("region", { name: "Rows" }).scrollTop).toBe(510);
    },
  );

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
      <Link href="/detail">Open Model</Link>
    </main>
  );
}

it.each([false, true])(
  "bounds folder reconstruction after eviction (failure=%s)",
  async (failed) => {
    let returning = false;
    const app = renderApp(<FolderBrowser />, {
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
    });
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
      <Link href="/another">Another Library</Link>
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

function HistoryLibrary({
  layout,
  anchorVisible,
}: {
  layout: LibraryLayout;
  anchorVisible: boolean;
}) {
  const entry = useLibraryEntry("/?type=all", true);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const reading = useLibraryReadingPosition(entry, layout, main, list, true, {
    ready: true,
    models: { count: 1, more: false, pending: false, failed: false, next: () => {} },
    folders: { count: 1, more: false, pending: false, failed: false, next: () => {} },
  });
  return (
    <main ref={main} aria-label="Library">
      <output aria-label="Reading status">{reading.status}</output>
      {anchorVisible && (
        <Link href="/detail" data-library-entry="/models/1">
          Reading anchor
        </Link>
      )}
      <Link href="/detail" data-library-entry="/models/2">
        Neighbor
      </Link>
    </main>
  );
}

function HistoryBrowser({ layout }: { layout: LibraryLayout }) {
  const location = useLocation();
  const navigate = useRouter();
  const [anchorVisible, setAnchorVisible] = useState(true);
  return (
    <>
      <button onClick={() => navigate.back()}>History Back</button>
      <button onClick={() => navigate.forward()}>History Forward</button>
      <button onClick={() => setAnchorVisible(false)}>Remove anchor</button>
      {location.pathname === "/detail" ? (
        <p>Model detail</p>
      ) : (
        <HistoryLibrary layout={layout} anchorVisible={anchorVisible} />
      )}
    </>
  );
}

function installReadingGeometry(header: { height: number }) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      if (!this.hasAttribute("data-library-entry") && !this.hasAttribute("data-library-anchor"))
        return new DOMRect(0, 0, 300, 500);
      const main = this.closest("main");
      if (!main) throw new Error("Reading geometry requires a Library container");
      const top = this.dataset.libraryEntry === "/models/2" ? 450 : 300;
      return new DOMRect(0, top + header.height - main.scrollTop, 300, 100);
    },
  );
}

describe("History departure reading anchor", () => {
  beforeEach(() => clearLogin());
  afterEach(() => vi.restoreAllMocks());

  it.each(["grid", "list"] as const)(
    "records a visible %s anchor on history departure after a layout shift",
    (layout) => {
      const header = { height: 0 };
      installReadingGeometry(header);
      renderApp(<HistoryBrowser layout={layout} />);
      const main = screen.getByRole("main");
      main.scrollTop = 200;
      fireEvent.scroll(main);
      fireEvent.click(screen.getByRole("link", { name: "Reading anchor" }));
      fireEvent.click(screen.getByRole("button", { name: "History Back" }));
      expect(screen.getByRole("link", { name: "Reading anchor" }).getBoundingClientRect().top).toBe(
        100,
      );

      header.height = 102;
      screen.getByRole("main").scrollTop = 302;
      fireEvent.scroll(screen.getByRole("main"));
      fireEvent.click(screen.getByRole("button", { name: "History Forward" }));
      header.height = 0;
      fireEvent.click(screen.getByRole("button", { name: "History Back" }));

      expect(screen.getByRole("link", { name: "Reading anchor" }).getBoundingClientRect().top).toBe(
        100,
      );
    },
  );

  it("resets when the selected history anchor is missing", () => {
    installReadingGeometry({ height: 0 });
    renderApp(<HistoryBrowser layout="grid" />);
    screen.getByRole("main").scrollTop = 200;
    fireEvent.scroll(screen.getByRole("main"));
    fireEvent.click(screen.getByRole("link", { name: "Reading anchor" }));
    fireEvent.click(screen.getByRole("button", { name: "History Back" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove anchor" }));
    fireEvent.click(screen.getByRole("button", { name: "History Forward" }));
    fireEvent.click(screen.getByRole("button", { name: "History Back" }));

    expect(screen.getByLabelText("Reading status")).toHaveTextContent("reset");
    expect(screen.getByRole("main").scrollTop).toBe(0);
  });
});

function VirtualReadingLibrary({
  missing,
  initiallyVisible,
  onOpen,
  kind = "model",
}: {
  missing: boolean;
  initiallyVisible: boolean;
  onOpen: () => void;
  kind?: "model" | "collection";
}) {
  const anchorKey = kind === "model" ? "/models/1" : "collection:1";
  const entry = useLibraryEntry("/", true);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const renderers = useRef(new Map<string, import("../reading-position").LibraryReadingRenderer>());
  const [visible, setVisible] = useState(initiallyVisible);
  useLayoutEffect(() => {
    renderers.current.set("models", {
      reveal(key) {
        if (missing || key !== anchorKey) return "missing";
        if (
          main.current?.querySelector(
            `[data-library-entry="${anchorKey}"], [data-library-anchor="${anchorKey}"]`,
          )
        )
          return "mounted";
        setVisible(true);
        return "pending";
      },
    });
  }, [missing, anchorKey]);
  const reading = useLibraryReadingPosition(
    entry,
    "grid",
    main,
    list,
    true,
    {
      ready: true,
      models: { count: 1, more: false, pending: false, failed: false, next: () => {} },
      folders: { count: 1, more: false, pending: false, failed: false, next: () => {} },
    },
    renderers,
  );
  return (
    <main ref={main}>
      <output aria-label="Virtual reading status">{reading.status}</output>
      {visible && (
        <Link
          href="/detail"
          data-library-entry={kind === "model" ? anchorKey : undefined}
          data-library-anchor={kind === "collection" ? anchorKey : undefined}
          onClick={onOpen}
        >
          Virtual anchor
        </Link>
      )}
    </main>
  );
}

function VirtualReadingBrowser({
  missing,
  kind = "model",
}: {
  missing: boolean;
  kind?: "model" | "collection";
}) {
  const location = useLocation();
  const [departed, setDeparted] = useState(false);
  return location.pathname === "/detail" ? (
    <Detail />
  ) : (
    <VirtualReadingLibrary
      missing={missing}
      kind={kind}
      initiallyVisible={!departed}
      onOpen={() => setDeparted(true)}
    />
  );
}

describe("Virtual reading anchors", () => {
  beforeEach(() => clearLogin());
  afterEach(() => vi.restoreAllMocks());
  it("mounts an offscreen anchor before restoring its offset", async () => {
    installReadingGeometry({ height: 0 });
    renderApp(<VirtualReadingBrowser missing={false} />);
    screen.getByRole("main").scrollTop = 200;
    fireEvent.click(screen.getByRole("link", { name: "Virtual anchor" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Virtual reading status")).toHaveTextContent("ready"),
    );
    expect(screen.getByRole("link", { name: "Virtual anchor" }).getBoundingClientRect().top).toBe(
      100,
    );
  });
  it("restores a Collection reading anchor after revealing its row", async () => {
    installReadingGeometry({ height: 0 });
    renderApp(<VirtualReadingBrowser missing={false} kind="collection" />);
    screen.getByRole("main").scrollTop = 200;
    fireEvent.click(screen.getByRole("link", { name: "Virtual anchor" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Virtual reading status")).toHaveTextContent("ready"),
    );
    expect(screen.getByRole("link", { name: "Virtual anchor" }).getBoundingClientRect().top).toBe(
      100,
    );
  });

  it("resets an anchor absent from the reconstructed loaded pages", async () => {
    installReadingGeometry({ height: 0 });
    renderApp(<VirtualReadingBrowser missing />);
    screen.getByRole("main").scrollTop = 200;
    fireEvent.click(screen.getByRole("link", { name: "Virtual anchor" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Virtual reading status")).toHaveTextContent("reset"),
    );
    expect(screen.getByRole("main").scrollTop).toBe(0);
  });
});
