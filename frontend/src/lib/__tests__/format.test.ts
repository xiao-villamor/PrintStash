/** Display formats follow the selected application locale, not browser defaults. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatBytes, formatNumber, timeAgo, timeAgoShort } from "@/lib/format";
import { formatCurrency } from "@/lib/currency";
import { setLocale } from "@/lib/locale";
afterEach(() => {
  vi.useRealTimers();
  setLocale("en");
});
describe("localized display", () => {
  it("formats values using the selected locale independently of the browser", () => {
    setLocale("es");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));
    expect(formatNumber(1.5)).toBe("1,5");
    expect(formatBytes(1536)).toBe("1,5 KB");
    expect(formatCurrency(12.5, "EUR")).toBe("12,50 €");
    expect(timeAgoShort("2026-06-13T06:00:00Z")).toBe("ayer");
    expect(timeAgo("2026-05-01T12:00:00Z")).toBe("1 may");
    setLocale("en");
    expect(formatNumber(1.5)).toBe("1.5");
  });

  it("uses localized names for display options", async () => {
    const { CARD_METRIC_OPTIONS } = await import("@/lib/card-metrics");
    const { METADATA_FIELDS } = await import("@/lib/metadata-preferences");
    setLocale("es");
    expect(CARD_METRIC_OPTIONS.find((option) => option.id === "file_count")?.label).toBe(
      "Nº de archivos",
    );
    expect(METADATA_FIELDS.find((option) => option.id === "printer_profile")?.label).toBe(
      "Perfil de impresora",
    );
  });
});
