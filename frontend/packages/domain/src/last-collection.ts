export const LAST_COLLECTION_STORAGE_KEY = "printstash.last.collection";
export const LAST_VIEW_STORAGE_KEY = "printstash.last.view";

/** Which tab of the vault the user last had open. */
export type LastView = "models" | "docs" | "multipart";

/**
 * The store the remembered vault context lives in, or null when there is none:
 * a server render, or a browser that refuses storage access outright.
 */
function contextStore(): Storage | null {
  if (!("localStorage" in globalThis)) return null;
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

/** Decode a persisted view string; anything we did not write reads as the model grid. */
function parseLastView(raw: string | null): LastView {
  return raw === "docs" || raw === "multipart" ? raw : "models";
}

export function rememberLastCollection(path: string | null): void {
  const store = contextStore();
  if (store === null) return;
  try {
    if (path) store.setItem(LAST_COLLECTION_STORAGE_KEY, path);
    else store.removeItem(LAST_COLLECTION_STORAGE_KEY);
  } catch {
    // Best-effort context restoration when browser storage is unavailable.
  }
}

export function readLastCollection(): string | null {
  const store = contextStore();
  if (store === null) return null;
  try {
    return store.getItem(LAST_COLLECTION_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function rememberLastView(view: LastView): void {
  const store = contextStore();
  if (store === null) return;
  try {
    store.setItem(LAST_VIEW_STORAGE_KEY, view);
  } catch {
    // Best-effort context restoration when browser storage is unavailable.
  }
}

export function readLastView(): LastView {
  const store = contextStore();
  if (store === null) return "models";
  try {
    return parseLastView(store.getItem(LAST_VIEW_STORAGE_KEY));
  } catch {
    return "models";
  }
}

export function lastVaultHref(): string {
  const parts: string[] = [];
  const path = readLastCollection();
  if (path) parts.push(`c=${encodeURIComponent(path)}`);
  if (readLastView() !== "models") parts.push(`v=${readLastView()}`);
  return parts.length ? `/?${parts.join("&")}` : "/";
}
