/** Reflowed items glide from their painted position; sizes and unmoved items stay put. */
import { act, render } from "@testing-library/react";
import { useLayoutEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useReflowGlide } from "../use-reflow-glide";

interface Box {
  left: number;
  top: number;
}
/** The layout each keyed item occupies, keyed by the grid state that produced it. */
const layouts = {
  full: { a: { left: 0, top: 0 }, b: { left: 300, top: 0 } },
  docked: { a: { left: 0, top: 0 }, b: { left: 0, top: 200 } },
} satisfies Record<string, Record<"a" | "b", Box>>;
type Layout = keyof typeof layouts;
let current: Layout = "full";
let reduced = false;
const animate =
  vi.fn<(frames: Keyframe[], options: KeyframeAnimationOptions) => Pick<Animation, "cancel">>();

/** Commits the geometry for `layout`, as CSS would once the DOM updates. */
function Geometry({ layout }: { layout: Layout }) {
  useLayoutEffect(() => {
    current = layout;
  }, [layout]);
  return null;
}

function Grid({ layout, geometry = layout }: { layout: Layout; geometry?: Layout }) {
  const [container, attach] = useState<HTMLElement | null>(null);
  useReflowGlide(container, layout);
  return (
    <main ref={attach} aria-label="Grid">
      <Geometry layout={geometry} />
      {(["a", "b"] as const).map((key) => (
        <article key={key} data-library-key={key} aria-label={key} />
      ))}
    </main>
  );
}

function rect({ left, top }: Box): DOMRect {
  return DOMRect.fromRect({ x: left, y: top, width: 100, height: 100 });
}

beforeEach(() => {
  current = "full";
  reduced = false;
  vi.stubGlobal("matchMedia", () => ({ matches: reduced }));
  vi.stubGlobal("requestAnimationFrame", () => 0);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  animate.mockReset().mockImplementation(() => ({ cancel: vi.fn<() => void>() }));
  Object.defineProperty(Element.prototype, "animate", { configurable: true, value: animate });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const key = this.getAttribute("data-library-key");
    if (key === null) return DOMRect.fromRect({ x: 0, y: 0, width: 1000, height: 1000 });
    if (key !== "a" && key !== "b") throw new Error(`unexpected item ${key}`);
    return rect(layouts[current][key]);
  });
  document.documentElement.style.setProperty("--duration-slow", "300ms");
  document.documentElement.style.setProperty("--ease-out", "ease-out");
});

afterEach(() => {
  Reflect.deleteProperty(Element.prototype, "animate");
  document.documentElement.removeAttribute("style");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useReflowGlide", () => {
  it("leaves the first layout in place", () => {
    render(<Grid layout="full" />);
    expect(animate).not.toHaveBeenCalled();
  });

  it("translates a moved item from its previous painted position", () => {
    const { rerender } = render(<Grid layout="full" />);
    act(() => rerender(<Grid layout="docked" />));
    expect(animate).toHaveBeenCalledTimes(1);
    const [frames, options] = animate.mock.calls[0]!;
    expect(frames).toEqual([
      { transform: "translate(300px, -200px)" },
      { transform: "translate(0, 0)" },
    ]);
    expect(options).toMatchObject({ duration: 300, easing: "ease-out", composite: "add" });
  });

  it("keeps a reflowed layout immediate under reduced motion", () => {
    reduced = true;
    const { rerender } = render(<Grid layout="full" />);
    act(() => rerender(<Grid layout="docked" />));
    expect(animate).not.toHaveBeenCalled();
  });

  it("ignores a layout change that moves nothing", () => {
    const { rerender } = render(<Grid layout="full" />);
    act(() => rerender(<Grid layout="docked" geometry="full" />));
    expect(animate).not.toHaveBeenCalled();
  });
});
