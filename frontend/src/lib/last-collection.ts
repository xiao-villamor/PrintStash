// Remembers the last vault collection (folder) the user was browsing so the
// PrintStash logo and post-delete navigation return there instead of resetting
// to "All Models". Collection selection lives in the URL as `?c=<path>`, but
// that param is dropped when the user navigates to Settings, a model, etc. —
// persisting it here lets us restore the context on the way back.
export const LAST_COLLECTION_STORAGE_KEY = "printstash.last.collection";

/** Persist the current collection path (or clear it at the root). Best-effort. */
export function rememberLastCollection(path: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (path) window.localStorage.setItem(LAST_COLLECTION_STORAGE_KEY, path);
    else window.localStorage.removeItem(LAST_COLLECTION_STORAGE_KEY);
  } catch {
    // Storage unavailable (private mode / disabled) — context restore is a
    // nicety, not a requirement, so silently skip it.
  }
}

/** Read the remembered collection path, or null if none/unavailable. */
export function readLastCollection(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(LAST_COLLECTION_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

/** Home URL that restores the last collection, e.g. `/?c=spoolers` (or `/`). */
export function lastVaultHref(): string {
  const path = readLastCollection();
  return path ? `/?c=${encodeURIComponent(path)}` : "/";
}
