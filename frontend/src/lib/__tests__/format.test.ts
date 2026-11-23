/** Display formats follow the selected application locale, not browser defaults. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatBytes, formatNumber, parseApiInstant, timeAgo, timeAgoShort } from "@/lib/format";
import { formatCurrency } from "@/lib/currency";
import { setLocale } from "@/lib/locale";
afterEach(() => {
  vi.useRealTimers();
  setLocale("en");
});
describe("localized display", () => {
  it.each([
    "2026-01-01T03:23:06.123456",
    "2026-01-01 03:23:06.123456",
    "2026-01-01T03:23:06.123456Z",
    "2025-12-31T19:23:06.123456-08:00",
    "2026-01-01T08:53:06.123456+05:30",
  ])("parses the API instant %s as the same UTC time", (value) => {
    expect(parseApiInstant(value)?.toISOString()).toBe("2026-01-01T03:23:06.123Z");
  });

  it.each([null, undefined, "", "2026-01-01", "2026-01-01T99:23:06", "unknown"])(
    "leaves non-instant input %s unparsed",
    (value) => expect(parseApiInstant(value)).toBeNull(),
  );

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
