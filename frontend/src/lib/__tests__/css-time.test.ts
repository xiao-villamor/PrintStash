import { describe, expect, it } from "vitest";
import { cssTimeSeconds } from "@/lib/css-time";

describe("cssTimeSeconds", () => {
  it("reads the authored millisecond form", () => {
    expect(cssTimeSeconds("300ms")).toBeCloseTo(0.3);
    expect(cssTimeSeconds(" 150ms ")).toBeCloseTo(0.15);
  });
  it("reads the minified second form that production CSS ships", () => {
    expect(cssTimeSeconds(".3s")).toBeCloseTo(0.3);
    expect(cssTimeSeconds("0.24s")).toBeCloseTo(0.24);
  });
  it("refuses a missing or unitless token instead of animating for no time", () => {
    expect(() => cssTimeSeconds("")).toThrow("invalid_css_time");
    expect(() => cssTimeSeconds("300")).toThrow("invalid_css_time");
  });
});
