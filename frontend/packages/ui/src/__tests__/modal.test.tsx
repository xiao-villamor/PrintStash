import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { Modal, ModalShell } from "../components/modal";

it("labels a titled modal and closes from its injected close button", () => {
  const onClose = vi.fn<() => void>();
  render(
    <Modal open onClose={onClose} closeLabel="Dismiss dialog" title="Edit">
      Contents
    </Modal>,
  );
  expect(screen.getByRole("dialog", { name: "Edit" })).toHaveTextContent("Contents");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss dialog" }));
  expect(onClose).toHaveBeenCalledOnce();
});

it("closes a shell on Escape and backdrop click", () => {
  const onClose = vi.fn<() => void>();
  render(
    <ModalShell open onClose={onClose} labelledBy="dialog-title">
      <h2 id="dialog-title">Dialog</h2>
    </ModalShell>,
  );
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("dialog").previousElementSibling!);
  expect(onClose).toHaveBeenCalledTimes(2);
});

it("traps focus in both directions and remains mounted for its exit", () => {
  vi.useFakeTimers();
  const onClose = vi.fn<() => void>();
  const { rerender } = render(
    <ModalShell open onClose={onClose} labelledBy="focus-title">
      <h2 id="focus-title">Focus</h2>
      <button>First</button>
      <button>Last</button>
    </ModalShell>,
  );
  const first = screen.getByRole("button", { name: "First" });
  const last = screen.getByRole("button", { name: "Last" });
  last.focus();
  fireEvent.keyDown(window, { key: "Tab" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
  expect(last).toHaveFocus();

  rerender(
    <ModalShell open={false} onClose={onClose} labelledBy="focus-title">
      <h2 id="focus-title">Focus</h2>
    </ModalShell>,
  );
  expect(screen.getByRole("dialog", { name: "Focus" })).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(200));
  expect(screen.queryByRole("dialog", { name: "Focus" })).not.toBeInTheDocument();
  vi.useRealTimers();
});
