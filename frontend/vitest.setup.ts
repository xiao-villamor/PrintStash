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

// Unmount React trees and clear localStorage between tests so state doesn't
// leak across cases (card metrics, metadata prefs, and auth all use storage).
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
