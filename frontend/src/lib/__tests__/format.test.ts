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
} from "@/lib/format";

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

describe("scalar formatters", () => {
  it("suffix formatters render an em dash for falsy values", () => {
    expect(formatMillimeters(0)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatGrams(null)).toBe("—");
    expect(formatTemperature(0)).toBe("—");
    expect(formatCost(0)).toBe("—");
  });

  it("apply their unit suffix to real values", () => {
    expect(formatMillimeters(0.2)).toBe("0.2mm");
    expect(formatPercent(20)).toBe("20%");
    expect(formatGrams(15)).toBe("15g");
    expect(formatTemperature(210)).toBe("210°C");
    expect(formatCost(24.5)).toBe("24.50");
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
});
