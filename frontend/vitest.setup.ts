import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees and clear localStorage between tests so state doesn't
// leak across cases (card metrics, metadata prefs, and auth all use storage).
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
