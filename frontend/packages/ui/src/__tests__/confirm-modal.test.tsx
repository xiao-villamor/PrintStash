import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ConfirmModal, type ConfirmModalProps } from "../components/confirm-modal";

function props(overrides: Partial<ConfirmModalProps> = {}): ConfirmModalProps {
  return {
    open: true,
    onClose: vi.fn<() => void>(),
    onConfirm: vi.fn<() => void>(),
    title: "Remove model?",
    description: "This cannot be undone.",
    closeLabel: "Dismiss dialog",
    cancelLabel: "Keep model",
    confirmLabel: "Remove model",
    ...overrides,
  };
}

it("uses injected labels and preserves both actions", () => {
  const modalProps = props();
  render(<ConfirmModal {...modalProps} />);
  fireEvent.click(screen.getByRole("button", { name: "Keep model" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove model" }));
  expect(modalProps.onClose).toHaveBeenCalledOnce();
  expect(modalProps.onConfirm).toHaveBeenCalledOnce();
});

it("disables both choices while the operation is busy", () => {
  render(<ConfirmModal {...props({ busy: true })} />);
  expect(screen.getByRole("button", { name: "Keep model" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove model" })).toBeDisabled();
});
