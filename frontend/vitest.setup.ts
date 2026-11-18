import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom implements pointer *events* but not pointer *capture*, and dnd-kit's
// pointer sensor calls it unconditionally on pointerdown. Without these the
// sensor throws asynchronously — the test still passes, but the run fails on an
// unhandled error, which is worse than a failing assertion because it points at
// no test in particular.
if (!("setPointerCapture" in Element.prototype)) {
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.hasPointerCapture = () => false;
}

// jsdom has no layout engine. Keep programmatic scroll offsets observable and
// allow components to subscribe to resize events in tests that supply geometry.
if (!("scrollTo" in Element.prototype)) {
  Element.prototype.scrollTo = function (optionsOrX?: ScrollToOptions | number, y?: number) {
    const options = optionsOrX === Number(optionsOrX) ? { left: optionsOrX, top: y } : optionsOrX;
    this.scrollLeft = options?.left ?? this.scrollLeft;
    this.scrollTop = options?.top ?? this.scrollTop;
  };
}

if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom has no media queries or animation clock. Ask for reduced motion, so content
// is shown at once, unless a test supplies its own matcher.
if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query) => ({
    matches: query.includes("prefers-reduced-motion: reduce"),
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}

// jsdom does not implement CSS.supports. Report features as unavailable, so code
// behind a support check takes its fallback unless a test opts in.
if ("CSS" in globalThis && !("supports" in CSS)) {
  Object.assign(CSS, { supports: () => false });
}

// Unmount React trees and clear localStorage between tests so state doesn't
// leak across cases (card metrics, metadata prefs, and auth all use storage).
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
