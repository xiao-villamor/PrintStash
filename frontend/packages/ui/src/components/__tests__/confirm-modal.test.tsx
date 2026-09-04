/*
 * The dialog standing between a user and every destructive action — deleting a model,
 * emptying the trash, removing a printer.
 *
 * Two things here have consequences. The confirm and cancel handlers must not be
 * transposed, which is the one bug in a confirmation dialog that destroys data
 * silently. And `busy` must disable both buttons while the request is in flight: a
 * confirm that stays clickable is a double-delete, and a cancel that stays clickable
 * dismisses the dialog while its request is still running.
 *
 * Every string is injected, because this package ships no user-visible copy.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmModal, type ConfirmModalProps } from "../confirm-modal";

const LABELS = {
  title: "Remove model?",
  description: "This cannot be undone.",
  closeLabel: "Dismiss dialog",
  cancelLabel: "Keep model",
  confirmLabel: "Remove model",
};

function open(props: Partial<ConfirmModalProps> = {}) {
  const handlers = { onClose: vi.fn<() => void>(), onConfirm: vi.fn<() => void>() };
  render(<ConfirmModal open {...LABELS} {...handlers} {...props} />);
  act(() => {
    vi.advanceTimersToNextFrame();
    vi.advanceTimersToNextFrame();
  });
  return handlers;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ConfirmModal", () => {
  it("states what is about to happen", () => {
    open();

    expect(screen.getByRole("heading", { name: "Remove model?" })).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("names the dialog after its own question", () => {
    open();

    const labelledBy = screen.getByRole("dialog").getAttribute("aria-labelledby");
    expect(document.getElementById(labelledBy!)).toHaveTextContent("Remove model?");
  });

  it("dismisses without acting when the cancel button is pressed", () => {
    const handlers = open();

    fireEvent.click(screen.getByRole("button", { name: "Keep model" }));

    expect(handlers.onClose).toHaveBeenCalledTimes(1);
    expect(handlers.onConfirm).not.toHaveBeenCalled();
  });

  it("acts when the confirm button is pressed", () => {
    const handlers = open();

    fireEvent.click(screen.getByRole("button", { name: "Remove model" }));

    expect(handlers.onConfirm).toHaveBeenCalledTimes(1);
    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("offers the injected close affordance", () => {
    const handlers = open();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss dialog" }));

    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });

  it("refuses a second confirmation while the first is in flight", () => {
    const handlers = open({ busy: true });

    fireEvent.click(screen.getByRole("button", { name: "Remove model" }));

    expect(handlers.onConfirm).not.toHaveBeenCalled();
  });

  it("refuses to cancel while the request is in flight", () => {
    const handlers = open({ busy: true });

    fireEvent.click(screen.getByRole("button", { name: "Keep model" }));

    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("shows the confirmation is in progress", () => {
    open({ busy: true });

    expect(
      screen.getByRole("button", { name: "Remove model" }).querySelector(".animate-spin"),
    ).toBeInTheDocument();
  });
});
