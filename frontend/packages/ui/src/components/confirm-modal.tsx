"use client";

import { useId } from "react";
import { AlertTriangle } from "lucide-react";

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
  const titleId = useId();
  return (
    <Modal
      open={open}
      onClose={onClose}
      closeLabel={closeLabel}
      labelledBy={titleId}
      className="max-w-sm"
    >
      <div className="flex flex-col items-center gap-4 text-center pb-2">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="h-6 w-6 text-destructive" />
        </div>
        <div className="min-w-0 max-w-full space-y-1">
          <h3 id={titleId} className="text-base font-semibold text-foreground">
            {title}
          </h3>
          <p className="wrap-anywhere text-sm text-muted-foreground">{description}</p>
        </div>
      </div>

      <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row">
        <Button
          type="button"
          variant="outline"
          onClick={onClose}
          disabled={busy}
          className="h-9 w-full font-mono uppercase tracking-wider text-muted-foreground sm:flex-1"
        >
          {cancelLabel}
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={onConfirm}
          loading={busy}
          className="h-9 w-full font-mono uppercase tracking-wider sm:flex-1"
        >
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
