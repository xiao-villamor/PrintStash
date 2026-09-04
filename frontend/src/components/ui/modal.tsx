"use client";

import {
  Modal as SharedModal,
  ModalShell,
  type ModalProps as SharedModalProps,
} from "@printstash/ui";

import { useOptionalI18n } from "@/lib/i18n";

export { ModalShell };
export type { ModalShellProps } from "@printstash/ui";

export type ModalProps = Omit<SharedModalProps, "closeLabel">;

export function Modal(props: ModalProps) {
  const closeLabel = useOptionalI18n()?.t("nav.close") ?? "Close";
  return <SharedModal {...props} closeLabel={closeLabel} />;
}
