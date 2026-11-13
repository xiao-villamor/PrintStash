"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { setThemeMode, useThemeMode } from "@/lib/theme";
import { Button } from "@/components/ui/button";
import { Moon, Sun } from "lucide-react";

export function ThemeToggle({ menuItem = false }: { menuItem?: boolean }) {
  useUiLocale();
  const mode = useThemeMode();
  const theme =
    mode === "system"
      ? document.documentElement.classList.contains("dark")
        ? "dark"
        : "light"
      : mode;
  function toggle() {
    setThemeMode(theme === "dark" ? "light" : "dark");
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      role={menuItem ? "menuitem" : undefined}
      className={menuItem ? "w-full justify-start gap-3 px-3 text-sm" : undefined}
      onClick={toggle}
      title={theme === "dark" ? uiText("Switch to light mode") : uiText("Switch to dark mode")}
      aria-label={uiText("Toggle theme")}
    >
      <span key={theme} className="animate-theme-icon inline-flex">
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </span>
      {menuItem && <span>{uiText("Toggle theme")}</span>}
    </Button>
  );
}
