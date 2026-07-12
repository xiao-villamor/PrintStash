export const PRINTER_CARD_IMAGE_STORAGE_KEY = "printstash.printer-card.show-image";

export function readPrinterCardImagePreference(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(PRINTER_CARD_IMAGE_STORAGE_KEY) === "true";
}

export function writePrinterCardImagePreference(showImage: boolean): void {
  window.localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, String(showImage));
}
