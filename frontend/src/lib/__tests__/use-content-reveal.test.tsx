/** Content reveals honor motion preferences, interruptions, and scroll restoration. */
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useContentReveal } from "../use-content-reveal";

class MotionPreference extends EventTarget {
  matches = false;
}
let preference: MotionPreference;
let finished: (() => void) | null;
const cancel = vi.fn<() => void>();
const animate = vi.fn<(frames: Keyframe[], options: KeyframeAnimationOptions) => object>();

function Content({
  identity = "first",
  ...options
}: { identity?: string } & NonNullable<Parameters<typeof useContentReveal>[1]>) {
  const ref = useContentReveal(identity, options);
  return (
    <section aria-label="Parent">
      <div ref={ref} role="region" aria-label="Content">
        {identity}
      </div>
    </section>
  );
}

function frames() {
  act(() => {
    vi.advanceTimersToNextFrame();
    vi.advanceTimersToNextFrame();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  preference = new MotionPreference();
  vi.stubGlobal("matchMedia", () => preference);
  cancel.mockClear();
  animate.mockReset().mockImplementation(() => ({
    cancel,
    get onfinish() {
      return finished;
    },
    set onfinish(callback: (() => void) | null) {
      finished = callback;
    },
  }));
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(performance.now()), 16),
  );
  vi.stubGlobal("cancelAnimationFrame", (id: number) => window.clearTimeout(id));
  Object.defineProperty(Element.prototype, "animate", { configurable: true, value: animate });
  document.documentElement.style.setProperty("--opacity-content-enter", "0.7");
  document.documentElement.style.setProperty("--duration-library-enter", "150ms");
  document.documentElement.style.setProperty("--ease-library-enter", "linear");
  document.documentElement.style.setProperty("--duration-press", "100ms");
  document.documentElement.style.setProperty("--ease-out", "ease-out");
});

afterEach(() => {
  Reflect.deleteProperty(Element.prototype, "animate");
  document.documentElement.removeAttribute("style");
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useContentReveal", () => {
  it("leaves the initial content opaque", () => {
    render(<Content />);
    frames();
    expect(animate).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "Content" }).style.opacity).toBe("");
  });

  it("clears the entrance override after a changed section finishes", () => {
    const { rerender } = render(<Content />);
    rerender(<Content identity="second" />);
    const content = screen.getByRole("region", { name: "Content" });
    expect(content.style.opacity).not.toBe("");
    frames();
    expect(animate).toHaveBeenCalledTimes(1);
    expect(animate.mock.calls[0][0].at(-1)).toEqual({ opacity: 1 });
    act(() => finished?.());
    expect(content.style.opacity).toBe("");
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("resumes an interrupted section at its visible opacity", () => {
    const { rerender } = render(<Content />);
    rerender(<Content identity="second" />);
    frames();
    screen.getByRole("region", { name: "Content" }).style.opacity = "0.8";
    rerender(<Content identity="third" />);
    expect(cancel).toHaveBeenCalledTimes(1);
    frames();
    expect(animate.mock.calls[1][0][0]).toEqual({ opacity: 0.8 });
  });

  it("settles immediately when reduced motion is enabled during entry", () => {
    render(<Content animateOnMount />);
    frames();
    act(() => {
      preference.matches = true;
      preference.dispatchEvent(new Event("change"));
    });
    expect(screen.getByRole("region", { name: "Content" }).style.opacity).toBe("");
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it.each(["self", "parent"] as const)(
    "resets the requested %s scroll surface on navigation",
    (scroll) => {
      const { rerender } = render(<Content scroll={scroll} />);
      const content = screen.getByRole("region", { name: "Content" });
      const parent = screen.getByRole("region", { name: "Parent" });
      content.scrollTop = parent.scrollTop = 100;
      rerender(<Content identity="second" scroll={scroll} />);
      expect(scroll === "self" ? content.scrollTop : parent.scrollTop).toBe(0);
      expect(scroll === "self" ? parent.scrollTop : content.scrollTop).toBe(100);
    },
  );

  it.each([
    { timing: "content" as const, duration: 150, easing: "linear" },
    { timing: "press" as const, duration: 100, easing: "ease-out" },
  ])("reveals a $timing handoff over its own timing tokens", ({ timing, duration, easing }) => {
    const { rerender } = render(<Content timing={timing} />);
    rerender(<Content identity="second" timing={timing} />);
    frames();
    expect(animate.mock.calls[0][1]).toMatchObject({ duration, easing });
  });

  it("does not animate content across unrelated scopes", () => {
    const { rerender } = render(<Content scope="one" />);
    rerender(<Content identity="second" scope="two" />);
    frames();
    expect(animate).not.toHaveBeenCalled();
  });
});
