/** Library grid resizing, card identity, and right-inspector motion. */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMemo, useRef, useState } from "react";
import { LibraryItems } from "@/components/library-items";
import { PANE_SETTLED_EVENT } from "@/components/use-collections-pane-motion";
import { InspectorWillMoveEvent } from "@/components/use-inspector-motion";
import type { LibraryReadingRenderer } from "@/features/library/reading-position";

let width = 1000;
const ITEMS = Array.from({ length: 6 }, (_, index) => index);

function Grid({
  count = 6,
  virtualize = false,
  keepCollectionCardWidth = false,
  singleRow = false,
  layout = "grid",
}: {
  count?: number;
  virtualize?: boolean;
  keepCollectionCardWidth?: boolean;
  singleRow?: boolean;
  layout?: "grid" | "list";
}) {
  // The scroller is known before the grid mounts, as in the Library.
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const scroll = useMemo(() => ({ current: node }), [node]);
  const renderers = useRef(new Map<string, LibraryReadingRenderer>());
  return (
    <div ref={setNode}>
      {node && (
        <LibraryItems
          items={count === 6 ? ITEMS : Array.from({ length: count }, (_, index) => index)}
          itemKey={(item) => `item:${item}`}
          renderItem={(item) => (
            <a href={`#${item}`} data-library-entry={`item:${item}`}>
              Card {item}
            </a>
          )}
          scrollRef={scroll}
          readingRenderers={renderers}
          readingId="test"
          layout={layout}
          virtualize={virtualize}
          keepCollectionCardWidth={keepCollectionCardWidth}
          singleRow={singleRow}
        />
      )}
    </div>
  );
}

/** Card flights by key; jsdom has no Web Animations. */
const flights = new Map<string, Keyframe[]>();
const animate = HTMLElement.prototype.animate;

const grid = () => document.querySelector<HTMLElement>(".library-virtual-row")!;

function enableCardMotion() {
  const matchMedia = window.matchMedia;
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    ...matchMedia(query),
    matches: false,
  }));
  const root = grid().parentElement!;
  root.style.setProperty("--duration-fast", "200ms");
  root.style.setProperty("--ease-dock", "ease-out");
  return root.parentElement!;
}

beforeEach(() => {
  width = 1000;
  HTMLElement.prototype.animate = function (this: HTMLElement, keyframes) {
    if (Array.isArray(keyframes)) flights.set(this.dataset.libraryKey!, keyframes);
    // SAFETY: the grid only calls cancel() on its flights.
    return { cancel() {} } as Animation;
  };
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => width);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  HTMLElement.prototype.animate = animate;
  flights.clear();
  delete document.documentElement.dataset.paneMoving;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("LibraryItems", () => {
  it("moves to the same column on the next virtualized row", () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(900);
    render(<Grid count={144} virtualize />);
    const first = screen.getByRole("link", { name: "Card 0" });
    act(() => first.focus());
    fireEvent.keyDown(first, { key: "ArrowDown" });

    expect(screen.getByRole("link", { name: "Card 3" })).toHaveFocus();
  });

  it("moves vertically through Library list results", () => {
    render(<Grid layout="list" />);
    const first = screen.getByRole("link", { name: "Card 0" });
    act(() => first.focus());
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(first).toHaveFocus();

    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(screen.getByRole("link", { name: "Card 1" })).toHaveFocus();
  });

  it("retains the focused card across the virtualization threshold", () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(900);
    const view = render(<Grid count={120} virtualize />);
    act(() => screen.getByRole("link", { name: "Card 0" }).focus());
    expect(screen.getByRole("link", { name: "Card 0" })).toHaveFocus();
    view.rerender(<Grid count={144} virtualize />);
    expect(document.querySelector("[data-library-renderer=virtual]")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Card 0" })).toHaveFocus();
    view.rerender(<Grid count={120} virtualize />);
    expect(screen.getByRole("link", { name: "Card 0" })).toHaveFocus();
  });

  it("leaves focus outside the grid when more cards arrive", () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(900);
    const view = render(
      <>
        <button>Outside</button>
        <Grid count={120} virtualize />
      </>,
    );
    act(() => screen.getByRole("link", { name: "Card 0" }).focus());
    act(() => screen.getByRole("button", { name: "Outside" }).focus());
    view.rerender(
      <>
        <button>Outside</button>
        <Grid count={144} virtualize />
      </>,
    );
    expect(screen.getByRole("button", { name: "Outside" })).toHaveFocus();
  });

  it("lays native cards out in one grid, so a column change keeps each card element", () => {
    render(<Grid />);
    expect(document.querySelectorAll(".library-virtual-row")).toHaveLength(1);
    expect(grid().style.gridTemplateColumns).toBe("repeat(3, 322px)");
    const card = screen.getByText("Card 5").closest("[data-library-key]");

    width = 1250;
    act(() => {
      window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
    });

    expect(screen.getByText("Card 5").closest("[data-library-key]")).toBe(card);
  });

  it("keeps Collections tracks fixed while the workspace is moving", () => {
    render(<Grid />);
    document.documentElement.dataset.paneMoving = "";
    width = 1250;

    act(() => window.dispatchEvent(new Event(PANE_SETTLED_EVENT)));

    expect(grid().style.gridTemplateColumns).toBe("repeat(3, 322px)");
    expect(flights.size).toBe(0);
  });

  it("keeps a collapsed strip on one row after its tracks narrow", () => {
    width = 1250;
    render(<Grid singleRow />);
    expect(screen.getByRole("link", { name: "Card 3" })).toBeVisible();
    width = 1000;

    act(() => window.dispatchEvent(new Event(PANE_SETTLED_EVENT)));

    expect(grid().style.gridTemplateColumns).toBe("repeat(3, 322px)");
    expect(screen.queryByRole("link", { name: "Card 3" })).toBeNull();
  });

  it("fades a newly visible card on close within the same column count", () => {
    render(
      <>
        <aside id="model-drawer" />
        <Grid />
      </>,
    );
    const scroller = enableCardMotion();
    const card = screen.getByText("Card 2").closest("[data-library-key]")!;
    vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1100, 500));
    vi.spyOn(document.getElementById("model-drawer")!, "getBoundingClientRect").mockReturnValue(
      new DOMRect(660, 0, 440, 500),
    );
    vi.spyOn(card, "getBoundingClientRect").mockImplementation(() =>
      width === 1000 ? new DOMRect(676, 0, 322, 200) : new DOMRect(706, 0, 337, 200),
    );

    act(() => {
      window.dispatchEvent(new InspectorWillMoveEvent("closing"));
      width = 1045;
      window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
    });

    expect(grid().style.gridTemplateColumns).toBe("repeat(3, 337px)");
    expect(flights.get("item:2")).toEqual([
      { opacity: 0.4, transform: "translateY(16px)" },
      { opacity: 1, transform: "none" },
    ]);
  });

  it("slides a card that wraps to the previous row on close", () => {
    render(<Grid />);
    const scroller = enableCardMotion();
    const card = screen.getByText("Card 3").closest("[data-library-key]")!;
    vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1250, 500));
    vi.spyOn(card, "getBoundingClientRect").mockImplementation(() =>
      width === 1000 ? new DOMRect(0, 216, 322, 200) : new DOMRect(951, 0, 300, 200),
    );

    act(() => {
      window.dispatchEvent(new InspectorWillMoveEvent("closing"));
      width = 1250;
      window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
    });

    expect(flights.get("item:3")).toEqual([
      { opacity: 0.65, transform: "translateY(16px)" },
      { opacity: 1, transform: "none" },
    ]);
  });

  it("preserves collection card width when the sidebar changes how many columns fit", () => {
    let paneWidth = 0;
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(
      function (this: HTMLElement) {
        return this.hasAttribute("data-collections-pane") ? paneWidth : width;
      },
    );
    width = 1150;
    render(
      <>
        <aside data-collections-pane />
        <Grid keepCollectionCardWidth />
      </>,
    );
    expect(grid().style.gridTemplateColumns).toBe("repeat(3, 372px)");
    for (const sidebarOpen of [true, false]) {
      paneWidth = sidebarOpen ? 320 : 0;
      width = 1150 - paneWidth;
      act(() => window.dispatchEvent(new Event(PANE_SETTLED_EVENT)));
      expect(grid().style.gridTemplateColumns).toBe(`repeat(${sidebarOpen ? 2 : 3}, 372px)`);
    }
  });

  it("keeps the viewport at the top when a lower grid changes width", async () => {
    render(<Grid />);
    const card = screen.getByText("Card 0").closest("[data-library-key]")!;
    const scroller = grid().closest("[data-library-renderer]")!.parentElement!;
    vi.spyOn(card, "getBoundingClientRect").mockImplementation(
      () =>
        new DOMRect(
          0,
          grid().style.gridTemplateColumns === "repeat(3, 322px)" ? 200 : 360,
          300,
          200,
        ),
    );
    width = 1250;
    act(() => {
      window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
    });
    await act(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
    expect(scroller.scrollTop).toBe(0);
  });
});
