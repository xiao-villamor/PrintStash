/**
 * Printer images are optional browser presentation. Storage failures must not
 * prevent a current-document choice from reaching mounted cards; healthy
 * persistence still permits external updates after a pending choice is saved.
 */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PRINTER_CARD_IMAGE_STORAGE_KEY,
  readPrinterCardImagePreference,
  usePrinterCardImagePreference,
  writePrinterCardImagePreference,
} from "@/lib/printer-card-display";

function blockProperty() {
  return vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
    throw new DOMException("blocked storage", "SecurityError");
  });
}
function blockRead() {
  return vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("blocked storage", "SecurityError");
  });
}
function blockWrite(errorName: string) {
  return vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("blocked storage", errorName);
  });
}
const blockedReads = [
  { failure: "property", block: blockProperty },
  { failure: "getItem", block: blockRead },
];
const blockedWrites = [
  { failure: "property", block: blockProperty },
  { failure: "setItem SecurityError", block: () => blockWrite("SecurityError") },
  { failure: "setItem quota", block: () => blockWrite("QuotaExceededError") },
];

beforeEach(() => {
  writePrinterCardImagePreference(false);
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  writePrinterCardImagePreference(false);
});

describe("readPrinterCardImagePreference", () => {
  it("hides printer images by default", () => {
    expect(readPrinterCardImagePreference()).toBe(false);
  });
  it.each(["false", "TRUE", "invalid"])(
    "rejects unsupported printer image preference %s",
    (stored) => {
      localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, stored);

      expect(readPrinterCardImagePreference()).toBe(false);
    },
  );
  it.each(blockedReads)(
    "defaults printer image choice when storage $failure is blocked",
    ({ block }) => {
      block();

      expect(readPrinterCardImagePreference()).toBe(false);
    },
  );
  it.each(blockedReads)(
    "retains read printer image choice when storage $failure is blocked",
    ({ block }) => {
      localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, "true");
      expect(readPrinterCardImagePreference()).toBe(true);
      block();

      expect(readPrinterCardImagePreference()).toBe(true);
    },
  );
});

describe("writePrinterCardImagePreference", () => {
  it.each([{ choice: true }, { choice: false }])(
    "persists printer image choice $choice",
    ({ choice }) => {
      writePrinterCardImagePreference(choice);

      expect(localStorage.getItem(PRINTER_CARD_IMAGE_STORAGE_KEY)).toBe(String(choice));
      expect(readPrinterCardImagePreference()).toBe(choice);
    },
  );
  it("resumes persisted printer image reads after storage recovery", () => {
    const spy = blockWrite("QuotaExceededError");
    writePrinterCardImagePreference(true);
    spy.mockRestore();

    writePrinterCardImagePreference(false);
    localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, "true");

    expect(readPrinterCardImagePreference()).toBe(true);
  });
  it.each(blockedWrites)(
    "retains selected printer image choice when storage $failure fails",
    ({ block }) => {
      writePrinterCardImagePreference(false);
      const spy = block();

      expect(() => writePrinterCardImagePreference(true)).not.toThrow();
      spy.mockRestore();

      expect(readPrinterCardImagePreference()).toBe(true);
      expect(readPrinterCardImagePreference()).toBe(true);
      expect(localStorage.getItem(PRINTER_CARD_IMAGE_STORAGE_KEY)).toBe("false");
    },
  );
});

describe("usePrinterCardImagePreference", () => {
  it("delivers selected printer image preference to mounted consumers", () => {
    const first = renderHook(usePrinterCardImagePreference);
    const second = renderHook(usePrinterCardImagePreference);

    act(() => writePrinterCardImagePreference(true));

    expect(first.result.current).toBe(true);
    expect(second.result.current).toBe(true);
  });
  it("receives external printer image preference changes", () => {
    const view = renderHook(usePrinterCardImagePreference);
    localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, "true");

    act(() =>
      window.dispatchEvent(new StorageEvent("storage", { key: PRINTER_CARD_IMAGE_STORAGE_KEY })),
    );

    expect(view.result.current).toBe(true);
  });
  it("ignores unrelated preference storage events", () => {
    const view = renderHook(usePrinterCardImagePreference);
    localStorage.setItem(PRINTER_CARD_IMAGE_STORAGE_KEY, "true");

    act(() => window.dispatchEvent(new StorageEvent("storage", { key: "other-preference" })));

    expect(view.result.current).toBe(false);
  });
  it("delivers unpersisted printer image preference to mounted consumers", () => {
    const first = renderHook(usePrinterCardImagePreference);
    const second = renderHook(usePrinterCardImagePreference);
    blockWrite("QuotaExceededError");

    act(() => writePrinterCardImagePreference(true));

    expect(first.result.current).toBe(true);
    expect(second.result.current).toBe(true);
  });
});
