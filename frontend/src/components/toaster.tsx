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
      containerAriaLabel={uiText("Messages")}
      closeButton
      gap={8}
      className="printstash-toaster !bottom-20 md:!bottom-4"
      toastOptions={{
        closeButtonAriaLabel: uiText("Close"),
        style: {
          fontFamily: "var(--font-sans), sans-serif",
          fontSize: "13px",
          borderRadius: "var(--radius-popover)",
          border: "1px solid var(--outline-variant)",
          background: "var(--popover)",
          color: "var(--popover-foreground)",
        },
      }}
    />
  );
}
