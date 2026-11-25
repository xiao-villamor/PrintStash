/* Embedded STL colours must stay attached to the facets that declared them. */
import { describe, expect, it } from "vitest";
import { embeddedStlColor } from "../embedded-stl-color";

describe("embeddedStlColor", () => {
  it("reads a uniform RGB marker only from a complete binary STL", () => {
    const buffer = new ArrayBuffer(134);
    new DataView(buffer).setUint32(80, 1, true);
    const bytes = new Uint8Array(buffer);
    const prefix = new TextEncoder().encode("PrintStash RGB:");
    expect(embeddedStlColor(buffer)).toBeUndefined();
    bytes.set(prefix);
    bytes.set([0, 128, 15], prefix.length);
    expect(embeddedStlColor(buffer)).toBe("#00800f");
    expect(embeddedStlColor(buffer.slice(0, 130))).toBeUndefined();
  });
});
