/** Library grid resizing, card identity, and right-inspector motion. */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMemo, useRef, useState } from "react";
import { LibraryItems } from "@/components/library-items";
import {
  INSPECTOR_LAYOUT_WILL_CHANGE_EVENT,
  PANE_SETTLED_EVENT,
  PaneMovingEvent,
} from "@/components/use-collections-pane-motion";
import type { LibraryReadingRenderer } from "@/features/library/reading-position";

let width = 1000;
const ITEMS = Array.from({ length: 6 }, (_, index) => index);

function Grid({
  count = 6,
  virtualize = false,
  keepCollectionCardWidth = false,
}: {
  count?: number;
  virtualize?: boolean;
  keepCollectionCardWidth?: boolean;
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
          renderItem={(item) => <a href={`#${item}`}>Card {item}</a>}
          scrollRef={scroll}
          readingRenderers={renderers}
          readingId="test"
          layout="grid"
          virtualize={virtualize}
          keepCollectionCardWidth={keepCollectionCardWidth}
        />
      )}
    </div>
  );
}

/** Card flights by key; jsdom has no Web Animations. */
const flights = new Map<string, Keyframe[]>();
const animate = HTMLElement.prototype.animate;

const grid = () => document.querySelector<HTMLElement>(".library-virtual-row")!;

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

  it("commits the resting columns as Collections starts closing", () => {
    render(
      <>
        <aside data-collections-pane />
        <Grid />
      </>,
    );
    const sidebar = document.querySelector<HTMLElement>("[data-collections-pane]")!;
    vi.spyOn(sidebar, "offsetWidth", "get").mockReturnValue(250);
    document.documentElement.dataset.paneMoving = "";

    act(() => {
      window.dispatchEvent(
        new PaneMovingEvent({
          targetSidebarWidth: 0,
          durationMs: 200,
          progress: [0, 0.5, 1],
        }),
      );
    });

    expect(grid().style.gridTemplateColumns).toBe("repeat(4, 300px)");
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

  it("keeps cards in place when inspector padding temporarily moves the grid", () => {
    render(<Grid />);
    const card = screen.getByText("Card 0").closest("[data-library-key]")!;
    vi.spyOn(card, "getBoundingClientRect").mockImplementation(
      () =>
        new DOMRect(
          0,
          width === 1250 && grid().style.gridTemplateColumns === "repeat(3, 322px)" ? 191 : 307,
          300,
          200,
        ),
    );

    act(() => {
      window.dispatchEvent(new Event(INSPECTOR_LAYOUT_WILL_CHANGE_EVENT));
      width = 1250;
      window.dispatchEvent(
        new PaneMovingEvent({
          targetSidebarWidth: null,
          durationMs: 200,
          progress: [0, 0.5, 1],
        }),
      );
    });

    expect(flights.has("item:0")).toBe(false);
  });

  it("carries a card that wraps a row in behind its new neighbour, without crossing the grid", () => {
    // Cards sit on the grid's current tracks (16 px gap), 200 px tall.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        const key = this.dataset.libraryKey;
        if (!key) return new DOMRect(0, 0, width, 800);
        const [, columns, track] = (
          /repeat\((\d+), (\d+)px\)/.exec(grid()?.style.gridTemplateColumns ?? "") ?? [0, 4, 300]
        ).map(Number);
        const index = Number(key.slice("item:".length));
        const pitch = track! + 16;
        return new DOMRect(
          (index % columns!) * pitch,
          Math.floor(index / columns!) * 216,
          track,
          200,
        );
      },
    );
    width = 1250;
    render(<Grid />);
    expect(grid().style.gridTemplateColumns).toBe("repeat(4, 300px)");
    width = 1000;
    act(() => {
      window.dispatchEvent(
        new PaneMovingEvent({
          targetSidebarWidth: null,
          durationMs: 200,
          progress: [0, 0.5, 1],
        }),
      );
    });
    expect(grid().style.gridTemplateColumns).toBe("repeat(3, 322px)");

    // 4 → 3 columns: card 5 keeps row 1 and slides one cell right.
    expect(flights.get("item:5")?.[0]).toMatchObject({ transform: "translate(-360px, 0px)" });
    // Card 3 wraps into the next row from beyond the left edge.
    expect(flights.get("item:3")?.[0]).toMatchObject({
      transform: "translate(-316px, 0px)",
      opacity: 0,
    });
    expect(flights.get("item:3")?.[1]).toMatchObject({ opacity: 1 });
    expect(flights.get("item:3")?.at(-1)).toMatchObject({ transform: "translate(0px, 0px)" });
    // Cards that keep their cell stay still.
    expect(flights.has("item:0")).toBe(false);
  });
});
