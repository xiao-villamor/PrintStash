/*
 * The dialog chrome behind every confirmation, editor, and picker in the product.
 *
 * `ModalShell` is where the accessibility contract of a modal actually lives: it
 * portals out of whatever card or overflow-hidden panel invoked it, marks itself
 * `aria-modal`, and points `aria-labelledby` at a real heading. Miss the portal and
 * the dialog is clipped by its ancestor; miss the label and a screen reader announces
 * an unnamed dialog and nothing else.
 *
 * `Modal` adds the titled chrome on top, and its close button's label is injected —
 * this package ships no user-visible strings — so "the caller's label reached the
 * button" is a contract rather than a detail. The focus trap, Escape, and scroll lock
 * come from `useOverlayBehavior` and are proven in its own tests; what is asserted
 * here is that this component wires them up at all.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DURATION } from "../../lib/overlay";
import { Modal, ModalShell } from "../modal";

/** Render and let the entrance frames run, as a browser would. */
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

describe("ModalShell", () => {
  it("renders nothing while closed", () => {
    render(
      <ModalShell open={false} onClose={vi.fn<() => void>()}>
        Contents
      </ModalShell>,
    );

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("is open unless the caller says otherwise", () => {
    open(<ModalShell onClose={vi.fn<() => void>()}>Contents</ModalShell>);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("portals out of the tree that rendered it", () => {
    const { container } = open(<ModalShell onClose={vi.fn<() => void>()}>Contents</ModalShell>);

    expect(container).toBeEmptyDOMElement();
    expect(document.body).toContainElement(screen.getByRole("dialog"));
  });

  it("marks its panel as a modal dialog", () => {
    open(<ModalShell onClose={vi.fn<() => void>()}>Contents</ModalShell>);

    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  });

  it("names itself after the element the caller nominated", () => {
    open(
      <ModalShell onClose={vi.fn<() => void>()} labelledBy="heading-id">
        <h2 id="heading-id">Edit model</h2>
      </ModalShell>,
    );

    expect(screen.getByRole("dialog")).toHaveAttribute("aria-labelledby", "heading-id");
  });

  it("closes when the backdrop is clicked", () => {
    const onClose = vi.fn<() => void>();
    open(<ModalShell onClose={onClose}>Contents</ModalShell>);

    fireEvent.click(backdrop()!);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn<() => void>();
    open(<ModalShell onClose={onClose}>Contents</ModalShell>);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the panel mounted while it animates out", () => {
    const { rerender } = open(<ModalShell onClose={vi.fn<() => void>()}>Contents</ModalShell>);

    rerender(
      <ModalShell open={false} onClose={vi.fn<() => void>()}>
        Contents
      </ModalShell>,
    );

    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closed");
  });

  it("removes the panel once the exit transition ends", () => {
    const { rerender } = open(<ModalShell onClose={vi.fn<() => void>()}>Contents</ModalShell>);

    rerender(
      <ModalShell open={false} onClose={vi.fn<() => void>()}>
        Contents
      </ModalShell>,
    );
    act(() => {
      vi.advanceTimersByTime(DURATION.fast);
    });

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lets a caller's class override the panel's own", () => {
    open(
      <ModalShell onClose={vi.fn<() => void>()} className="max-w-xs">
        Contents
      </ModalShell>,
    );

    expect(screen.getByRole("dialog")).toHaveClass("max-w-xs");
  });
});

describe("Modal", () => {
  it("renders its children", () => {
    open(
      <Modal open onClose={vi.fn<() => void>()} closeLabel="Dismiss dialog">
        Contents
      </Modal>,
    );

    expect(screen.getByText("Contents")).toBeInTheDocument();
  });

  it("renders the title as a heading", () => {
    open(
      <Modal open onClose={vi.fn<() => void>()} closeLabel="Dismiss dialog" title="Edit model">
        Contents
      </Modal>,
    );

    expect(screen.getByRole("heading", { name: "Edit model" })).toBeInTheDocument();
  });

  it("names the dialog after its own title", () => {
    open(
      <Modal open onClose={vi.fn<() => void>()} closeLabel="Dismiss dialog" title="Edit model">
        Contents
      </Modal>,
    );

    const labelledBy = screen.getByRole("dialog").getAttribute("aria-labelledby");
    expect(document.getElementById(labelledBy!)).toHaveTextContent("Edit model");
  });

  it("prefers a label the caller nominated over its own title", () => {
    open(
      <Modal
        open
        onClose={vi.fn<() => void>()}
        closeLabel="Dismiss dialog"
        title="Edit"
        labelledBy="other-id"
      >
        Contents
      </Modal>,
    );

    expect(screen.getByRole("dialog")).toHaveAttribute("aria-labelledby", "other-id");
  });

  it("leaves an untitled dialog unnamed rather than pointing at nothing", () => {
    open(
      <Modal open onClose={vi.fn<() => void>()} closeLabel="Dismiss dialog">
        Contents
      </Modal>,
    );

    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-labelledby");
  });

  it("labels its close button with the string the application injected", () => {
    open(
      <Modal open onClose={vi.fn<() => void>()} closeLabel="Dismiss dialog">
        Contents
      </Modal>,
    );

    expect(screen.getByRole("button", { name: "Dismiss dialog" })).toBeInTheDocument();
  });

  it("closes when the close button is pressed", () => {
    const onClose = vi.fn<() => void>();
    open(
      <Modal open onClose={onClose} closeLabel="Dismiss dialog" title="Edit model">
        Contents
      </Modal>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dismiss dialog" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("still offers a close button without a title", () => {
    open(
      <Modal open onClose={vi.fn<() => void>()} closeLabel="Dismiss dialog">
        Contents
      </Modal>,
    );

    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByRole("button", { name: "Dismiss dialog" })).toBeInTheDocument();
  });
});
