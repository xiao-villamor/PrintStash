"use client";

import { useId } from "react";
import { AlertTriangle } from "lucide-react";
import { Modal } from "./modal";
import { Button } from "./button";
import { translateUiText } from "./localized";
import { useOptionalI18n } from "@/lib/i18n";

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Delete",
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
  busy?: boolean;
}) {
  const titleId = useId();
  const locale = useOptionalI18n()?.locale ?? "en";
  const localizedTitle = translateUiText(locale, title);
  const localizedDescription = translateUiText(locale, description);
  const localizedConfirmLabel = translateUiText(locale, confirmLabel);
  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-sm">
      <div className="flex flex-col items-center gap-4 text-center pb-2">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="h-6 w-6 text-destructive" />
        </div>
        <div className="space-y-1">
          <h3 id={titleId} className="text-base font-semibold text-foreground">{localizedTitle}</h3>
          <p className="text-sm text-muted-foreground">{localizedDescription}</p>
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
          {translateUiText(locale, "Cancel")}
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={onConfirm}
          loading={busy}
          className="h-9 w-full font-mono uppercase tracking-wider sm:flex-1"
        >
          {localizedConfirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
