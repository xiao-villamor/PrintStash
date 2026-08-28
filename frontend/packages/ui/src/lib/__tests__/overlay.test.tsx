/*
 * The two hooks every overlay in the product is built out of — the modal, the
 * drawer, the confirm dialog, the dropdown menu.
 *
 * `useMountTransition` exists because an element cannot transition from styles it
 * was never painted with. It keeps the panel mounted for the whole exit window and
 * holds it at "closed" for the first frames after it opens, so the enter transition
 * has somewhere to come from. Break either half and DESIGN.md's motion contract
 * silently degrades to a pop-in — nothing throws, nothing renders wrong, and no
 * screenshot test would catch it.
 *
 * `useOverlayBehavior` is the accessibility contract: focus moves into the panel,
 * Tab cannot escape it, Escape closes it, the page behind it does not scroll, and
 * focus comes back to whatever opened it. Every one of those is invisible to a
 * mouse user and load-bearing for a keyboard or screen-reader one, which is exactly
 * the kind of regression that ships unnoticed.
 */

import { act, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMountTransition, useOverlayBehavior } from "../overlay";

const EXIT_MS = 200;

/** A panel wired exactly as the real overlays wire it: ref, hook, conditional render. */
function Overlay({
  open,
  onClose,
  children,
}: {
  open: boolean;
  onClose: () => void;
  children?: React.ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  useOverlayBehavior(open, onClose, panelRef);
  if (!open) return null;
  return (
    <div ref={panelRef} tabIndex={-1} data-testid="panel">
      {children}
    </div>
  );
}

/** A transition harness that reports what the hook returns for the current render. */
function Transition({ open, exitMs }: { open: boolean; exitMs: number }) {
  const { mounted, state } = useMountTransition(open, exitMs);
  return <div data-testid="probe" data-mounted={String(mounted)} data-state={state} />;
}

function probe() {
  return screen.getByTestId("probe");
}

/** Dispatch a real keydown on `window`, where the hook listens. */
function press(key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  act(() => {
    window.dispatchEvent(event);
  });
  return event;
}

describe("useMountTransition", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("mounts the panel in the same commit that opens it", () => {
    render(<Transition open={true} exitMs={EXIT_MS} />);

    expect(probe()).toHaveAttribute("data-mounted", "true");
  });

  it("holds the panel at closed until the browser has painted it", () => {
    render(<Transition open={true} exitMs={EXIT_MS} />);

    expect(probe()).toHaveAttribute("data-state", "closed");
  });

  it("switches to open once two frames have passed", () => {
    render(<Transition open={true} exitMs={EXIT_MS} />);

    act(() => {
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
    });

    expect(probe()).toHaveAttribute("data-state", "open");
  });

  it("leaves a never-opened panel unmounted", () => {
    render(<Transition open={false} exitMs={EXIT_MS} />);

    expect(probe()).toHaveAttribute("data-mounted", "false");
  });

  it("keeps the panel mounted while the exit transition plays", () => {
    const { rerender } = render(<Transition open={true} exitMs={EXIT_MS} />);

    rerender(<Transition open={false} exitMs={EXIT_MS} />);
    act(() => {
      vi.advanceTimersByTime(EXIT_MS - 1);
    });

    expect(probe()).toHaveAttribute("data-mounted", "true");
  });

  it("reports closed for the whole exit window", () => {
    const { rerender } = render(<Transition open={true} exitMs={EXIT_MS} />);
    act(() => {
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
    });

    rerender(<Transition open={false} exitMs={EXIT_MS} />);

    expect(probe()).toHaveAttribute("data-state", "closed");
  });

  it("unmounts the panel once the exit window elapses", () => {
    const { rerender } = render(<Transition open={true} exitMs={EXIT_MS} />);

    rerender(<Transition open={false} exitMs={EXIT_MS} />);
    act(() => {
      vi.advanceTimersByTime(EXIT_MS);
    });

    expect(probe()).toHaveAttribute("data-mounted", "false");
  });

  it("re-arms the enter transition when it reopens mid-exit", () => {
    const { rerender } = render(<Transition open={true} exitMs={EXIT_MS} />);
    act(() => {
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
    });
    rerender(<Transition open={false} exitMs={EXIT_MS} />);

    rerender(<Transition open={true} exitMs={EXIT_MS} />);

    expect(probe()).toHaveAttribute("data-state", "closed");
  });
});

describe("useOverlayBehavior", () => {
  it("moves focus into the panel when it opens", () => {
    render(<Overlay open={true} onClose={vi.fn<() => void>()} />);

    expect(screen.getByTestId("panel")).toHaveFocus();
  });

  it("prefers an element the panel marked autofocus", () => {
    render(
      <Overlay open={true} onClose={vi.fn<() => void>()}>
        <button type="button">First</button>
        <button
          type="button"
          ref={(el) => {
            el?.setAttribute("autofocus", "");
          }}
        >
          Confirm
        </button>
      </Overlay>,
    );

    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn<() => void>();
    render(<Overlay open={true} onClose={onClose} />);

    press("Escape");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("locks the page behind it from scrolling", () => {
    render(<Overlay open={true} onClose={vi.fn<() => void>()} />);

    expect(document.body.style.overflow).toBe("hidden");
  });

  it("unlocks page scrolling when it closes", () => {
    const { rerender } = render(<Overlay open={true} onClose={vi.fn<() => void>()} />);

    rerender(<Overlay open={false} onClose={vi.fn<() => void>()} />);

    expect(document.body.style.overflow).toBe("");
  });

  it("returns focus to whatever opened it", () => {
    render(<button type="button">Opener</button>);
    const opener = screen.getByRole("button", { name: "Opener" });
    opener.focus();
    const { rerender } = render(<Overlay open={true} onClose={vi.fn<() => void>()} />);

    rerender(<Overlay open={false} onClose={vi.fn<() => void>()} />);

    expect(opener).toHaveFocus();
  });

  it("skips restoring focus to an opener that is not an HTML element", () => {
    // An SVG element is focusable but is not an HTMLElement, which is the case the
    // restore guard exists for: `document.activeElement` is typed `Element | null`.
    render(
      <svg tabIndex={-1}>
        <title>Chart</title>
      </svg>,
    );
    const opener = document.querySelector("svg");
    opener?.focus();
    const { rerender } = render(<Overlay open={true} onClose={vi.fn<() => void>()} />);

    rerender(<Overlay open={false} onClose={vi.fn<() => void>()} />);

    expect(opener).not.toHaveFocus();
  });

  it("wraps Tab from the last focusable back to the first", () => {
    render(
      <Overlay open={true} onClose={vi.fn<() => void>()}>
        <button type="button">First</button>
        <button type="button">Last</button>
      </Overlay>,
    );
    screen.getByRole("button", { name: "Last" }).focus();

    const event = press("Tab");

    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
    expect(event.defaultPrevented).toBe(true);
  });

  it("wraps Shift+Tab from the first focusable back to the last", () => {
    render(
      <Overlay open={true} onClose={vi.fn<() => void>()}>
        <button type="button">First</button>
        <button type="button">Last</button>
      </Overlay>,
    );
    screen.getByRole("button", { name: "First" }).focus();

    const event = press("Tab", { shiftKey: true });

    expect(screen.getByRole("button", { name: "Last" })).toHaveFocus();
    expect(event.defaultPrevented).toBe(true);
  });

  it("wraps Shift+Tab from the panel itself to the last focusable", () => {
    render(
      <Overlay open={true} onClose={vi.fn<() => void>()}>
        <button type="button">First</button>
        <button type="button">Last</button>
      </Overlay>,
    );

    press("Tab", { shiftKey: true });

    expect(screen.getByRole("button", { name: "Last" })).toHaveFocus();
  });

  it("leaves a Tab in the middle of the panel to the browser", () => {
    render(
      <Overlay open={true} onClose={vi.fn<() => void>()}>
        <button type="button">First</button>
        <button type="button">Middle</button>
        <button type="button">Last</button>
      </Overlay>,
    );
    const middle = screen.getByRole("button", { name: "Middle" });
    middle.focus();

    const event = press("Tab");

    expect(event.defaultPrevented).toBe(false);
    expect(middle).toHaveFocus();
  });

  it("swallows Tab when the panel has nothing focusable", () => {
    render(
      <Overlay open={true} onClose={vi.fn<() => void>()}>
        <p>Nothing to focus here.</p>
      </Overlay>,
    );

    const event = press("Tab");

    expect(event.defaultPrevented).toBe(true);
  });

  it("ignores keys it does not handle", () => {
    const onClose = vi.fn<() => void>();
    render(
      <Overlay open={true} onClose={onClose}>
        <button type="button">First</button>
      </Overlay>,
    );

    const event = press("a");

    expect(event.defaultPrevented).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("stays inert while the overlay is closed", () => {
    const onClose = vi.fn<() => void>();
    render(<Overlay open={false} onClose={onClose} />);

    press("Escape");

    expect(onClose).not.toHaveBeenCalled();
    expect(document.body.style.overflow).toBe("");
  });

  it("calls the latest onClose after the caller replaces it", () => {
    const first = vi.fn<() => void>();
    const second = vi.fn<() => void>();
    const { rerender } = render(<Overlay open={true} onClose={first} />);

    rerender(<Overlay open={true} onClose={second} />);
    press("Escape");

    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });
});
