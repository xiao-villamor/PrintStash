"use client";

import { useState } from "react";
import { Moon, Sun } from "lucide-react";

const STORAGE_KEY = "printstash.theme";
const LEGACY_STORAGE_KEY = "nexus3d.theme";
type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.add("theme-transitioning");
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
  const favicon = document.getElementById("app-favicon") as HTMLLinkElement | null;
  if (favicon) {
    favicon.href = theme === "dark" ? "/icon-dark.svg?v=2" : "/icon-light.svg?v=2";
  }
  const id = window.setTimeout(() => root.classList.remove("theme-transitioning"), 350);
  return () => window.clearTimeout(id);
}

export function ThemeToggle() {
  // The pre-paint script in index.html already resolved the theme onto <html>;
  // read it back rather than re-resolving, so state can never disagree with the DOM.
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem(STORAGE_KEY, next);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    applyTheme(next);
  }

  return (
    <button
      onClick={toggle}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-label="Toggle theme"
      className="text-muted-foreground hover:text-primary transition-colors flex items-center justify-center font-mono"
    >
      <span key={theme} className="animate-theme-icon inline-flex">
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </span>
    </button>
  );
}
