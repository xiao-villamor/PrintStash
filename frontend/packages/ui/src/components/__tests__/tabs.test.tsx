/*
 * The tab bar behind every sectioned page — model detail, settings, the printer
 * console.
 *
 * It is a hand-rolled ARIA tablist, so the parts a mouse never touches are the
 * parts that break. Roving tabindex is what puts the whole bar on one Tab stop:
 * exactly one button may be reachable, and the arrow keys move between them.
 * Get that wrong and a keyboard user either cannot reach the tabs at all or has to
 * Tab through every one of them to leave the bar.
 *
 * The sliding underline is measured from real layout, which jsdom does not have —
 * so these tests stub `offsetLeft`/`offsetWidth` and assert on the transform the
 * component derives from them. That is the whole of the indicator's contract: it
 * tracks the active tab, it re-measures when the bar reflows, and it disappears
 * when there is no active tab rather than pointing at the wrong one.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Defined before the module import so `tabs.tsx`'s module-level feature detect
// sees it, exactly as it would in a browser.
const resizeObservers = vi.hoisted(() => {
  const instances: { reflow: () => void; disconnected: boolean }[] = [];
  class FakeResizeObserver implements ResizeObserver {
    private entry: { reflow: () => void; disconnected: boolean };
    constructor(callback: ResizeObserverCallback) {
      this.entry = { reflow: () => callback([], this), disconnected: false };
      instances.push(this.entry);
    }
    observe() {}
    unobserve() {}
    disconnect() {
      this.entry.disconnected = true;
    }
  }
  globalThis.ResizeObserver = FakeResizeObserver;
  return instances;
});

import { TabBar } from "../tabs";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "files", label: "Files" },
  { key: "history", label: "History" },
];

/** jsdom has no layout engine, so the geometry the indicator measures is stubbed. */
const LAYOUT = new Map([
  ["Overview", { left: 0, width: 100 }],
  ["Files", { left: 100, width: 60 }],
  ["History", { left: 160, width: 80 }],
]);
let layout = new Map(LAYOUT);

beforeEach(() => {
  layout = new Map(LAYOUT);
  resizeObservers.length = 0;
  vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(
    function (this: HTMLElement) {
      return layout.get(this.textContent ?? "")?.left ?? 0;
    },
  );
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(
    function (this: HTMLElement) {
      return layout.get(this.textContent ?? "")?.width ?? 0;
    },
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

function indicator() {
  return document.querySelector<HTMLElement>('[role="tablist"] > span[aria-hidden]');
}

/** The bar as pages use it: `active` is state the parent owns. */
function ControlledTabBar({ initial = "overview" }: { initial?: string }) {
  const [active, setActive] = useState(initial);
  return <TabBar tabs={TABS} active={active} onChange={setActive} />;
}

describe("TabBar", () => {
  describe("rendering", () => {
    it("renders one tab per item inside a tablist", () => {
      render(<TabBar tabs={TABS} active="overview" onChange={vi.fn<(key: string) => void>()} />);

      expect(screen.getByRole("tablist")).toBeInTheDocument();
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
        "Overview",
        "Files",
        "History",
      ]);
    });

    it("marks only the active tab as selected", () => {
      render(<TabBar tabs={TABS} active="files" onChange={vi.fn<(key: string) => void>()} />);

      expect(screen.getAllByRole("tab").map((tab) => tab.getAttribute("aria-selected"))).toEqual([
        "false",
        "true",
        "false",
      ]);
    });

    it("puts the whole bar on a single tab stop", () => {
      render(<TabBar tabs={TABS} active="files" onChange={vi.fn<(key: string) => void>()} />);

      expect(screen.getAllByRole("tab").map((tab) => tab.tabIndex)).toEqual([-1, 0, -1]);
    });

    it("applies the active class only to the active tab", () => {
      render(
        <TabBar
          tabs={TABS}
          active="files"
          onChange={vi.fn<(key: string) => void>()}
          tabClassName="tab"
          activeTabClassName="tab-on"
        />,
      );

      expect(screen.getByRole("tab", { name: "Files" })).toHaveClass("tab", "tab-on");
      expect(screen.getByRole("tab", { name: "Overview" })).not.toHaveClass("tab-on");
    });
  });

  describe("selection", () => {
    it("reports the key of a clicked tab", () => {
      const onChange = vi.fn<(key: string) => void>();
      render(<TabBar tabs={TABS} active="overview" onChange={onChange} />);

      fireEvent.click(screen.getByRole("tab", { name: "History" }));

      expect(onChange).toHaveBeenCalledWith("history");
    });

    it("moves to the next tab on ArrowRight", () => {
      const onChange = vi.fn<(key: string) => void>();
      render(<TabBar tabs={TABS} active="overview" onChange={onChange} />);

      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });

      expect(onChange).toHaveBeenCalledWith("files");
    });

    it("wraps past the last tab to the first", () => {
      const onChange = vi.fn<(key: string) => void>();
      render(<TabBar tabs={TABS} active="history" onChange={onChange} />);

      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });

      expect(onChange).toHaveBeenCalledWith("overview");
    });

    it("moves to the previous tab on ArrowLeft", () => {
      const onChange = vi.fn<(key: string) => void>();
      render(<TabBar tabs={TABS} active="files" onChange={onChange} />);

      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });

      expect(onChange).toHaveBeenCalledWith("overview");
    });

    it("wraps before the first tab to the last", () => {
      const onChange = vi.fn<(key: string) => void>();
      render(<TabBar tabs={TABS} active="overview" onChange={onChange} />);

      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });

      expect(onChange).toHaveBeenCalledWith("history");
    });

    it("keeps an arrow key from scrolling the page", () => {
      render(<TabBar tabs={TABS} active="overview" onChange={vi.fn<(key: string) => void>()} />);

      const notCancelled = fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });

      expect(notCancelled).toBe(false);
    });

    it("leaves keys it does not own to the browser", () => {
      const onChange = vi.fn<(key: string) => void>();
      render(<TabBar tabs={TABS} active="overview" onChange={onChange} />);

      const notCancelled = fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });

      expect(notCancelled).toBe(true);
      expect(onChange).not.toHaveBeenCalled();
    });

    it("carries focus to the tab an arrow key selected", () => {
      vi.useFakeTimers();
      try {
        render(<ControlledTabBar />);

        fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
        vi.advanceTimersToNextFrame();

        expect(screen.getByRole("tab", { name: "Files" })).toHaveFocus();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("indicator", () => {
    it("sits under the active tab", () => {
      render(<TabBar tabs={TABS} active="files" onChange={vi.fn<(key: string) => void>()} />);

      expect(indicator()).toHaveStyle({ transform: "translateX(100px) scaleX(60)" });
    });

    it("insets by the requested amount on both sides", () => {
      render(
        <TabBar
          tabs={TABS}
          active="files"
          onChange={vi.fn<(key: string) => void>()}
          indicatorInset={8}
        />,
      );

      expect(indicator()).toHaveStyle({ transform: "translateX(108px) scaleX(44)" });
    });

    it("never inverts on a tab narrower than its inset", () => {
      render(
        <TabBar
          tabs={TABS}
          active="files"
          onChange={vi.fn<(key: string) => void>()}
          indicatorInset={40}
        />,
      );

      expect(indicator()).toHaveStyle({ transform: "translateX(140px) scaleX(0)" });
    });

    it("follows the active tab when it changes", () => {
      const { rerender } = render(
        <TabBar tabs={TABS} active="files" onChange={vi.fn<(key: string) => void>()} />,
      );

      rerender(<TabBar tabs={TABS} active="history" onChange={vi.fn<(key: string) => void>()} />);

      expect(indicator()).toHaveStyle({ transform: "translateX(160px) scaleX(80)" });
    });

    it("re-measures when the bar reflows", () => {
      render(<TabBar tabs={TABS} active="files" onChange={vi.fn<(key: string) => void>()} />);

      layout.set("Files", { left: 240, width: 90 });
      act(() => {
        resizeObservers.at(-1)?.reflow();
      });

      expect(indicator()).toHaveStyle({ transform: "translateX(240px) scaleX(90)" });
    });

    it("disappears when no tab is active", () => {
      render(<TabBar tabs={TABS} active="nothing" onChange={vi.fn<(key: string) => void>()} />);

      expect(indicator()).toBeNull();
    });

    it("stays hidden when the caller suppresses it", () => {
      render(
        <TabBar
          tabs={TABS}
          active="files"
          onChange={vi.fn<(key: string) => void>()}
          showIndicator={false}
        />,
      );

      expect(indicator()).toBeNull();
    });

    it("stops observing the bar when it unmounts", () => {
      const { unmount } = render(
        <TabBar tabs={TABS} active="files" onChange={vi.fn<(key: string) => void>()} />,
      );

      unmount();

      expect(resizeObservers.at(-1)?.disconnected).toBe(true);
    });
  });
});
