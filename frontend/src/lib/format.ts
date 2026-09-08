import * as display from "@printstash/domain/format";
import { currentLocale } from "./locale";

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
  return new Intl.NumberFormat(currentLocale(), options).format(value);
}
