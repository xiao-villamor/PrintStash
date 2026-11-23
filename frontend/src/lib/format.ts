import * as display from "@printstash/domain/format";
import { currentLocale } from "./locale";

/** SQLite can omit the UTC offset when serializing API timestamps. */
export function parseApiInstant(value: string | null | undefined): Date | null {
  if (!value) return null;
  const iso = value.replace(" ", "T");
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/.exec(iso);
  if (!match) return null;
  const [, time, fraction = "", offset = "Z"] = match;
  const milliseconds = fraction.slice(0, 3).padEnd(3, "0");
  const date = new Date(`${time}.${milliseconds}${offset}`);
  return Number.isFinite(date.getTime()) ? date : null;
}

export const formatBytes = (value: number | null | undefined) =>
  display.formatBytes(value, currentLocale());
export const formatDuration = (value: number | null | undefined) =>
  display.formatDuration(value, currentLocale());
export const formatMillimeters = (value: number | null | undefined) =>
  display.formatMillimeters(value, currentLocale());
export const formatPercent = (value: number | null | undefined) =>
  display.formatPercent(value, currentLocale());
export const formatGrams = (value: number | null | undefined) =>
  display.formatGrams(value, currentLocale());
export const formatTemperature = (value: number | null | undefined) =>
  display.formatTemperature(value, currentLocale());
export const formatCost = (value: number | null | undefined) =>
  display.formatCost(value, currentLocale());
export const timeAgo = (value: string) => display.timeAgo(value, currentLocale());
export const timeAgoShort = (value: string) => display.timeAgoShort(value, currentLocale());

export function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
  return display.numberFormat(currentLocale(), options).format(value);
}
export const formatCalendarDate = (value: string) =>
  display.formatCalendarDate(value, currentLocale());

export function dispatchName(filename: string) {
  return filename.replace(/__vault-f\d+-[a-f0-9]{8,64}(?=\.[^.]+$)/i, "");
}
