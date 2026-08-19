import { useSyncExternalStore } from "react";

export const PRINTER_CARD_IMAGE_STORAGE_KEY = "printstash.printer-card.show-image";
const PRINTER_CARD_IMAGE_EVENT = "printstash:printer-card-image-changed";

export function readPrinterCardImagePreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(PRINTER_CARD_IMAGE_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function writePrinterCardImagePreference(showImage: boolean): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, String(showImage));
  window.dispatchEvent(new Event(PRINTER_CARD_IMAGE_EVENT));
}

function subscribePrinterCardImagePreference(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
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
