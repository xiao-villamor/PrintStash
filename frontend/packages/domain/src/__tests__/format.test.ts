/*
 * The em dash is the contract. Every formatter here answers the same question —
 * "what do I show when there is no value?" — and the answer must never be a
 * number.
 *
 * `formatGrams(null)` rendering "0 g" tells a user their print used no filament,
 * which is a fact they might act on; the em dash tells them PrintStash does not
 * know, which is the truth. That distinction is why the zero cases are split
 * per formatter rather than parametrized: percent and temperature have a
 * meaningful zero (0% complete, 0 °C), and grams, millimetres and cost do not —
 * a 0 there only ever comes from a field nothing filled in.
 *
 * The unit-scaling and duration cases exist because a wrong boundary is silently
 * plausible: 1023 bytes shown as "1.0 KB" or 59 minutes as "0h 59m" both read as
 * correct until somebody compares two models side by side.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  formatBytes,
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
  timeAgo,
  timeAgoShort,
} from "../format";

describe("formatBytes", () => {
  it("renders an em dash for null/undefined", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
  });

  it("renders exactly 0 B", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("scales into the right unit and rounds to one decimal", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(1024 * 1024)).toBe("1 MB");
    expect(formatBytes(5.5 * 1024 * 1024 * 1024)).toBe("5.5 GB");
  });

  it("caps at the largest known unit (TB)", () => {
    expect(formatBytes(1024 ** 5)).toContain("TB");
  });
});

describe("formatDuration", () => {
  it("renders an em dash for zero or negative", () => {
    expect(formatDuration(0)).toBe("—");
    expect(formatDuration(-5)).toBe("—");
    expect(formatDuration(null)).toBe("—");
  });

  it("uses h+m above an hour", () => {
    expect(formatDuration(3661)).toBe("1h 1m");
  });

  it("uses m+s below an hour", () => {
    expect(formatDuration(125)).toBe("2m 5s");
  });

  it("uses seconds only below a minute", () => {
    expect(formatDuration(42)).toBe("42s");
  });
});

describe("formatPercent, formatTemperature, formatGrams, formatMillimeters, formatCost", () => {
  it("render an em dash for null/undefined", () => {
    expect(formatMillimeters(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatPercent(null)).toBe("—");
    expect(formatGrams(null)).toBe("—");
    expect(formatTemperature(undefined)).toBe("—");
  });

  it("apply their unit suffix to real values", () => {
    expect(formatMillimeters(0.2)).toBe("0.2mm");
    expect(formatPercent(20)).toBe("20%");
    expect(formatPercent(88.88888888888889)).toBe("88.9%");
    expect(formatGrams(15)).toBe("15g");
    expect(formatGrams(1231.0000000000002)).toBe("1,231g");
    expect(formatTemperature(210)).toBe("210°C");
    expect(formatCost(24.5)).toBe("24.50");
  });

  it("preserve a meaningful zero for percent and temperature", () => {
    // 0% infill (vase mode) and a 0 °C unheated bed are real measurements, not
    // missing data — they must not collapse to an em dash.
    expect(formatPercent(0)).toBe("0%");
    expect(formatTemperature(0)).toBe("0°C");
  });

  it("treat a zero as missing for grams/mm/cost (0 there means no data)", () => {
    expect(formatGrams(0)).toBe("—");
    expect(formatMillimeters(0)).toBe("—");
    expect(formatCost(0)).toBe("—");
  });
});

describe("timeAgo", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function freezeAt(iso: string) {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(iso));
  }

  it("collapses sub-minute differences to 'just now'", () => {
    freezeAt("2026-06-14T12:00:30Z");
    expect(timeAgo("2026-06-14T12:00:00Z")).toBe("just now");
  });

  it("renders minutes, hours, and days", () => {
    freezeAt("2026-06-14T12:00:00Z");
    expect(timeAgo("2026-06-14T11:45:00Z")).toBe("15m ago");
    expect(timeAgo("2026-06-14T09:00:00Z")).toBe("3h ago");
    expect(timeAgo("2026-06-11T12:00:00Z")).toBe("3d ago");
  });

  it("falls back to an absolute date beyond a week", () => {
    freezeAt("2026-06-14T12:00:00Z");
    expect(timeAgo("2026-05-01T12:00:00Z")).toMatch(/May/);
  });

  it("timeAgoShort uses words for today/yesterday", () => {
    freezeAt("2026-06-14T12:00:00Z");
    expect(timeAgoShort("2026-06-14T06:00:00Z")).toBe("Today");
    expect(timeAgoShort("2026-06-13T06:00:00Z")).toBe("Yesterday");
    expect(timeAgoShort("2026-06-11T12:00:00Z")).toBe("3 days ago");
  });

  it("timeAgoShort falls back to a date past the week it counts in days", () => {
    // Beyond a week "N days ago" stops being something a reader can place, so
    // the absolute date takes over.
    freezeAt("2026-06-14T12:00:00Z");

    expect(timeAgoShort("2026-05-01T12:00:00Z")).toBe("May 1");
  });
});
