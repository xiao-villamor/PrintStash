"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const STORAGE_KEY = "printstash.theme";
const LEGACY_STORAGE_KEY = "nexus3d.theme";
type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.add("theme-transitioning");
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
  const id = window.setTimeout(() => root.classList.remove("theme-transitioning"), 350);
  return () => window.clearTimeout(id);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const stored =
      (localStorage.getItem(STORAGE_KEY) as Theme | null) ??
      (localStorage.getItem(LEGACY_STORAGE_KEY) as Theme | null) ??
      (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
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

  if (!mounted) return <div className="h-9 w-9" aria-hidden />;

  return (
    <button
      onClick={toggle}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-label="Toggle theme"
      className="text-muted-foreground hover:text-blue-600 dark:text-orange-500 transition-all flex items-center justify-center font-mono"
    >
      <span key={theme} className="animate-theme-icon inline-flex">
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </span>
    </button>
  );
}
