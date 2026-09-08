"use client";

import { Toaster as SonnerToaster } from "sonner";
import { uiText, localeDefinitions } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

export function Toaster() {
  const locale = useUiLocale();
  return (
    <SonnerToaster
      position="bottom-right"
      dir={localeDefinitions[locale].direction}
      containerAriaLabel={uiText("Notifications")}
      className="!bottom-20 md:!bottom-4 group toast"
      toastOptions={{
        closeButtonAriaLabel: uiText("Close"),
        style: {
          fontFamily: "var(--font-mono), monospace",
          fontSize: "13px",
          border: "1px solid var(--outline-variant)",
          background: "var(--surface-container-lowest)",
          color: "var(--on-surface)",
        },
      }}
    />
  );
}
