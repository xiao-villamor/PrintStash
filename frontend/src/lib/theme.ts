import { useEffect, useSyncExternalStore } from "react";

export type ThemeMode = "light" | "dark" | "system";
const CHANGE_EVENT = "printstash:theme";
let transientMode: ThemeMode | null = null;

function readTheme(): ThemeMode {
  try {
    const stored =
      localStorage.getItem("printstash.theme") ?? localStorage.getItem("nexus3d.theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    return transientMode ?? "system";
  }
  return transientMode ?? "system";
}

function subscribe(callback: () => void) {
  window.addEventListener(CHANGE_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(CHANGE_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

function applyTheme(mode: ThemeMode) {
  const dark =
    mode === "dark" || (mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  const favicon = document.getElementById("app-favicon");
  if (favicon instanceof HTMLLinkElement)
    favicon.href = dark ? "/icon-dark.svg?v=2" : "/icon-light.svg?v=2";
}

export function setThemeMode(mode: ThemeMode) {
  try {
    localStorage.setItem("printstash.theme", mode);
    localStorage.removeItem("nexus3d.theme");
    transientMode = null;
  } catch {
    transientMode = mode;
  }
  applyTheme(mode);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function useThemeMode(): ThemeMode {
  return useSyncExternalStore<ThemeMode>(subscribe, readTheme, () => "system");
}

/** Keep the System preference live while menus and their controls are closed. */
export function useSystemTheme() {
  const mode = useThemeMode();
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const update = () => applyTheme(mode);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [mode]);
}
