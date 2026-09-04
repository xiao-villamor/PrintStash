/*
 * The sliding panel used for mobile navigation and the bottom action sheet.
 *
 * It is the same dialog contract as `Modal` — portalled, `aria-modal`, backdrop
 * dismiss, Escape, focus trap — with one addition that is entirely its own: the side
 * it slides from decides its transform, and a panel given the wrong side animates in
 * from off-screen in the wrong direction or, worse, never becomes visible at all.
 *
 * Its label is injected rather than derived from a title, because a drawer has no
 * heading chrome; if that prop stops reaching the panel the drawer is an unnamed
 * dialog to every screen reader.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DURATION } from "../../lib/overlay";
import { Drawer } from "../drawer";

function open(ui: React.ReactElement) {
  const result = render(ui);
  act(() => {
    vi.advanceTimersToNextFrame();
    vi.advanceTimersToNextFrame();
  });
  return result;
}

function backdrop() {
  return document.querySelector<HTMLElement>('[aria-hidden="true"][data-state]');
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Drawer", () => {
  it("renders nothing while closed", () => {
    render(
      <Drawer open={false} onClose={vi.fn<() => void>()} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("portals a modal dialog named by the injected label", () => {
    const { container } = open(
      <Drawer open onClose={vi.fn<() => void>()} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.getByRole("dialog", { name: "Navigation" })).toHaveAttribute(
      "aria-modal",
      "true",
    );
  });

  it("slides in from the left edge", () => {
    open(
      <Drawer open onClose={vi.fn<() => void>()} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );

    expect(screen.getByRole("dialog")).toHaveClass(
      "left-0",
      "data-[state=closed]:-translate-x-full",
    );
  });

  it("slides up from the bottom edge", () => {
    open(
      <Drawer open onClose={vi.fn<() => void>()} side="bottom" ariaLabel="Actions">
        Links
      </Drawer>,
    );

    expect(screen.getByRole("dialog")).toHaveClass(
      "bottom-0",
      "data-[state=closed]:translate-y-full",
    );
  });

  it("closes when the backdrop is clicked", () => {
    const onClose = vi.fn<() => void>();
    open(
      <Drawer open onClose={onClose} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );

    fireEvent.click(backdrop()!);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn<() => void>();
    open(
      <Drawer open onClose={onClose} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("removes the panel once the exit transition ends", () => {
    const { rerender } = open(
      <Drawer open onClose={vi.fn<() => void>()} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );

    rerender(
      <Drawer open={false} onClose={vi.fn<() => void>()} side="left" ariaLabel="Navigation">
        Links
      </Drawer>,
    );
    act(() => {
      vi.advanceTimersByTime(DURATION.fast);
    });

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lets a caller class the panel and its container", () => {
    open(
      <Drawer
        open
        onClose={vi.fn<() => void>()}
        side="left"
        ariaLabel="Navigation"
        containerClassName="z-modal"
        className="w-72"
      >
        Links
      </Drawer>,
    );

    expect(screen.getByRole("dialog")).toHaveClass("w-72");
    expect(screen.getByRole("dialog").parentElement).toHaveClass("z-modal");
  });
});
