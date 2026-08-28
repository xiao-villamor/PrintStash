/*
 * `cn` is how every component in this package accepts a `className` from its
 * caller, so it is the single point where "the consumer overrides the primitive"
 * either works or silently doesn't.
 *
 * It is `clsx` for conditional class lists plus `tailwind-merge` for conflict
 * resolution, and the merge is the load-bearing half: without it a caller passing
 * `px-8` to a button whose base is `px-4` gets both, and which one wins is decided
 * by stylesheet order rather than by the caller. That failure is invisible in a
 * DOM assertion — the class *is* there — which is why it is asserted here directly.
 */

import { describe, expect, it } from "vitest";

import { cn } from "../utils";

describe("cn", () => {
  it("joins plain class names", () => {
    expect(cn("rounded", "border")).toBe("rounded border");
  });

  it("lets the last of two conflicting utilities win", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
  });

  it("keeps utilities that do not conflict", () => {
    expect(cn("px-2", "py-4")).toBe("px-2 py-4");
  });

  it("drops falsy entries", () => {
    expect(cn("border", false, null, undefined, "")).toBe("border");
  });

  it("applies a conditional class only when its flag is set", () => {
    expect(cn("base", { active: true, muted: false })).toBe("base active");
  });

  it("flattens nested lists", () => {
    expect(cn(["a", ["b", "c"]])).toBe("a b c");
  });

  it("returns an empty string when given nothing", () => {
    expect(cn()).toBe("");
  });
});
