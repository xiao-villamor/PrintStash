"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const STORAGE_KEY = "printstash.theme";
const LEGACY_STORAGE_KEY = "nexus3d.theme";
type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const stored =
      (localStorage.getItem(STORAGE_KEY) as Theme | null) ??
      (localStorage.getItem(LEGACY_STORAGE_KEY) as Theme | null) ??
      "light";
    setTheme(stored);
    setMounted(true);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem(STORAGE_KEY, next);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    applyTheme(next);
  }

  // Avoid hydration mismatch — render a placeholder until mounted.
  if (!mounted) {
    return <div className="h-9 w-9" aria-hidden />;
  }

  return (
    <button
      onClick={toggle}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-label="Toggle theme"
      className="inline-flex h-9 w-9 items-center justify-center rounded text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container)] hover:text-[var(--primary)]"
    >
      {theme === "dark" ? (
        <Sun className="h-5 w-5" />
      ) : (
        <Moon className="h-5 w-5" />
      )}
    </button>
  );
}
