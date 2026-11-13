"use client";

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
}: ConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      closeLabel={closeLabel}
      title={title}
      className="max-w-120"
      footer={
        <>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="wrap-anywhere text-sm leading-normal text-muted-foreground">{description}</p>
    </Modal>
  );
}
