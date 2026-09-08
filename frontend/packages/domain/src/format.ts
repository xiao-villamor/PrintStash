/**
 * Shared display formatters. The single home for value-to-string rules used
 * across model detail, grids, printer pages, and upload flows.
 */

export function formatBytes(bytes: number | null | undefined, locale = "en"): string {
  if (bytes == null) return "—";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return (
    new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(bytes / Math.pow(k, i)) +
    " " +
    sizes[i]
  );
}

export function formatDuration(seconds: number | null | undefined, locale = "en"): string {
  if (!seconds || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const unit = (value: number, name: string) =>
    new Intl.NumberFormat(locale, { style: "unit", unit: name, unitDisplay: "narrow" }).format(
      value,
    );
  if (h > 0) return `${unit(h, "hour")} ${unit(m, "minute")}`;
  if (m > 0) return `${unit(m, "minute")} ${unit(s, "second")}`;
  return unit(s, "second");
}

export function formatMillimeters(value: number | null | undefined, locale = "en"): string {
  return value
    ? `${new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value)}mm`
    : "—";
}

export function formatPercent(value: number | null | undefined, locale = "en"): string {
  return value == null ? "—" : `${formatDecimal(value, locale)}%`;
}

export function formatGrams(value: number | null | undefined, locale = "en"): string {
  return value ? `${formatDecimal(value, locale)}g` : "—";
}

function formatDecimal(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value);
}

export function formatTemperature(value: number | null | undefined, locale = "en"): string {
  return value == null ? "—" : `${formatDecimal(value, locale)}°C`;
}

export function formatCost(value: number | null | undefined, locale = "en"): string {
  return value
    ? new Intl.NumberFormat(locale, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
        useGrouping: false,
      }).format(value)
    : "—";
}

export function timeAgo(dateStr: string, locale = "en"): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "narrow" });
  if (mins < 1) return relative.format(0, "second");
  if (mins < 60) return relative.format(-mins, "minute");
  const hours = Math.floor(mins / 60);
  if (hours < 24) return relative.format(-hours, "hour");
  const days = Math.floor(hours / 24);
  if (days < 7) return relative.format(-days, "day");
  return new Date(dateStr).toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
  });
}

/** Variant used on cards: collapses today/yesterday into words. */
export function timeAgoShort(dateStr: string, locale = "en"): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (mins < 1) return relative.format(0, "second");
  if (mins < 60)
    return new Intl.RelativeTimeFormat(locale, { style: "narrow" }).format(-mins, "minute");
  const hours = Math.floor(mins / 60);
  if (hours < 24) return relative.format(0, "day");
  const days = Math.floor(hours / 24);
  if (days < 7) return relative.format(-days, "day");
  return new Date(dateStr).toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
  });
}
