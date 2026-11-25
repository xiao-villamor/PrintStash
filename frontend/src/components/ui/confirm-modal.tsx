"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import {
  ConfirmModal as SharedConfirmModal,
  type ConfirmModalProps as SharedConfirmModalProps,
} from "@printstash/ui";

import { useOptionalI18n } from "@/lib/i18n";
import { translateUiText } from "@/lib/locale";

export interface ConfirmModalProps extends Omit<
  SharedConfirmModalProps,
  "closeLabel" | "cancelLabel" | "confirmLabel"
> {
  confirmLabel?: string;
}

export function ConfirmModal({
  title,
  description,
  confirmLabel = "Delete",
  ...props
}: ConfirmModalProps) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const locale = i18n?.locale ?? "en";
  return (
    <SharedConfirmModal
      {...props}
      title={translateUiText(locale, title)}
      description={translateUiText(locale, description)}
      closeLabel={i18n?.t("nav.close") ?? uiText("Close")}
      cancelLabel={translateUiText(locale, "Cancel")}
      confirmLabel={translateUiText(locale, confirmLabel)}
    />
  );
}
