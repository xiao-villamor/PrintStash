/** The Library grid reflows with the Collections pane as one motion, keeping card identity. */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMemo, useRef, useState } from "react";
import { LibraryItems } from "@/components/library-items";
import { PANE_SETTLED_EVENT, PaneMovingEvent } from "@/components/use-collections-pane-motion";
import type { LibraryReadingRenderer } from "@/features/library/reading-position";

let width = 1000;
const ITEMS = Array.from({ length: 6 }, (_, index) => index);

function Grid() {
  // The scroller is known before the grid mounts, as in the Library.
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const scroll = useMemo(() => ({ current: node }), [node]);
  const renderers = useRef(new Map<string, LibraryReadingRenderer>());
  return (
    <div ref={setNode}>
      {node && (
        <LibraryItems
          items={ITEMS}
          itemKey={(item) => `item:${item}`}
          renderItem={(item) => <span>Card {item}</span>}
          scrollRef={scroll}
          readingRenderers={renderers}
          readingId="test"
          layout="grid"
          virtualize={false}
        />
      )}
    </div>
  );
}

const grid = () => document.querySelector<HTMLElement>(".library-virtual-row")!;

beforeEach(() => {
  width = 1000;
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
  delete document.documentElement.dataset.paneMoving;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("LibraryItems", () => {
  it("lays native cards out in one grid, so a column change keeps each card element", () => {
    render(<Grid />);
    expect(document.querySelectorAll(".library-virtual-row")).toHaveLength(1);
    expect(grid().style.gridTemplateColumns).toBe("repeat(4, 238px)");
    const card = screen.getByText("Card 5").closest("[data-library-key]");

    width = 1250;
    document.documentElement.dataset.paneMoving = "";
    act(() => {
      window.dispatchEvent(
        new PaneMovingEvent({
          opening: false,
          contentDelta: 250,
          durationMs: 300,
          progress: [0, 0.5, 1],
        }),
      );
    });

    expect(screen.getByText("Card 5").closest("[data-library-key]")).toBe(card);
  });

  it("switches to the resting columns when the pane starts moving, not when it rests", () => {
    render(<Grid />);
    document.documentElement.dataset.paneMoving = "";
    act(() => {
      window.dispatchEvent(
        new PaneMovingEvent({
          opening: false,
          contentDelta: 250,
          durationMs: 300,
          progress: [0, 0.5, 1],
        }),
      );
    });
    // The content is still 1000 px wide; the grid already holds 1250 px of tracks.
    expect(grid().style.gridTemplateColumns).toBe("repeat(5, 237px)");

    width = 1250;
    delete document.documentElement.dataset.paneMoving;
    act(() => {
      window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
    });
    expect(grid().style.gridTemplateColumns).toBe("repeat(5, 237px)");
  });
});
