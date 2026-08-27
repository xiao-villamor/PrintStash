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

describe("display formatters", () => {
  afterEach(() => vi.useRealTimers());

  it("preserves byte, duration, scalar, and missing-value rules", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(1024 ** 5)).toContain("TB");
    expect(formatDuration(3661)).toBe("1h 1m");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatDuration(0)).toBe("—");
    expect(formatMillimeters(0.2)).toBe("0.2mm");
    expect(formatMillimeters(0)).toBe("—");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(88.88888888888889)).toBe("88.9%");
    expect(formatGrams(1231.0000000000002)).toBe("1,231g");
    expect(formatGrams(0)).toBe("—");
    expect(formatTemperature(0)).toBe("0°C");
    expect(formatCost(24.5)).toBe("24.50");
    expect(formatCost(0)).toBe("—");
  });

  it("preserves relative-time cutoffs and absolute fallback", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));

    expect(timeAgo("2026-06-14T11:45:00Z")).toBe("15m ago");
    expect(timeAgo("2026-06-14T09:00:00Z")).toBe("3h ago");
    expect(timeAgo("2026-06-11T12:00:00Z")).toBe("3d ago");
    expect(timeAgo("2026-05-01T12:00:00Z")).toMatch(/May/);
    expect(timeAgoShort("2026-06-14T06:00:00Z")).toBe("Today");
    expect(timeAgoShort("2026-06-13T06:00:00Z")).toBe("Yesterday");
  });
});
