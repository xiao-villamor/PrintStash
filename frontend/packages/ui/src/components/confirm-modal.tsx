"use client";

import type { ReactNode } from "react";

import { Button } from "./button";
import { Modal } from "./modal";

export interface ConfirmModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  /** Labels are injected so this shared primitive remains locale-neutral. */
  closeLabel: string;
  cancelLabel: string;
  confirmLabel: string;
  busy?: boolean;
  variant?: "default" | "destructive";
  confirmDisabled?: boolean;
  children?: ReactNode;
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  closeLabel,
  cancelLabel,
  confirmLabel,
  busy = false,
  variant = "destructive",
  confirmDisabled = false,
  children,
}: ConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      closeLabel={closeLabel}
      title={title}
      description={description}
      variant="confirmation"
      dismissible={!busy}
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={busy}
            data-autofocus={busy ? undefined : ""}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={variant}
            onClick={onConfirm}
            loading={busy}
            disabled={confirmDisabled}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  );
}
