"use client";

import { ConfirmModal as SharedConfirmModal } from "@printstash/ui";

import { useOptionalI18n } from "@/lib/i18n";
import { translateUiText } from "./localized";

export interface ConfirmModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
  busy?: boolean;
}

export function ConfirmModal({
  title,
  description,
  confirmLabel = "Delete",
  ...props
}: ConfirmModalProps) {
  const i18n = useOptionalI18n();
  const locale = i18n?.locale ?? "en";
  return (
    <SharedConfirmModal
      {...props}
      title={translateUiText(locale, title)}
      description={translateUiText(locale, description)}
      closeLabel={i18n?.t("nav.close") ?? "Close"}
      cancelLabel={translateUiText(locale, "Cancel")}
      confirmLabel={translateUiText(locale, confirmLabel)}
    />
  );
}
