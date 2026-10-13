import { useSyncExternalStore } from "react";

export const PRINTER_CARD_IMAGE_STORAGE_KEY = "printstash.printer-card.show-image";
const PRINTER_CARD_IMAGE_EVENT = "printstash:printer-card-image-changed";

/**
 * Whether a DOM is available. `localStorage`, `dispatchEvent` and the event
 * listeners below only exist inside a browser document; during a non-browser
 * render (prerender/SSR, node test runner without jsdom) there is no `window`.
 */
const isBrowser = (): boolean => "window" in globalThis;

// The current document retains a choice while optional persistence is unavailable.
let documentChoice: { value: boolean; pending: boolean } | null = null;

export function readPrinterCardImagePreference(): boolean {
  if (!isBrowser()) return false;
  if (documentChoice?.pending) return documentChoice.value;
  try {
    const value = window.localStorage.getItem(PRINTER_CARD_IMAGE_STORAGE_KEY) === "true";
    documentChoice = { value, pending: false };
    return value;
  } catch {
    return documentChoice?.value ?? false;
  }
}

export function writePrinterCardImagePreference(showImage: boolean): void {
  if (!isBrowser()) return;
  const raw = String(showImage);
  documentChoice = { value: showImage, pending: true };
  try {
    window.localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, raw);
    documentChoice.pending = false;
  } catch {
    // A blocked/quota-limited store cannot prevent the same-document update.
  }
  window.dispatchEvent(new Event(PRINTER_CARD_IMAGE_EVENT));
}

function subscribePrinterCardImagePreference(onChange: () => void): () => void {
  if (!isBrowser()) return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === PRINTER_CARD_IMAGE_STORAGE_KEY) onChange();
  };
  window.addEventListener(PRINTER_CARD_IMAGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(PRINTER_CARD_IMAGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function usePrinterCardImagePreference(): boolean {
  return useSyncExternalStore(
    subscribePrinterCardImagePreference,
    readPrinterCardImagePreference,
    () => false,
  );
}
