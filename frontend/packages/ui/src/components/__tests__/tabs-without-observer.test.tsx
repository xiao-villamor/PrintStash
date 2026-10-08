/** Geometry remains usable in environments without resize observation. */
import { render } from "@testing-library/react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { descriptor } = vi.hoisted(() => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "ResizeObserver");
  Reflect.deleteProperty(globalThis, "ResizeObserver");
  return { descriptor };
});

import { TabBar } from "../tabs";

const tabs = [
  { key: "overview", label: "Overview" },
  { key: "files", label: "Files" },
];

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(
    function (this: HTMLElement) {
      return this.textContent === "Files" ? 100 : 0;
    },
  );
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(
    function (this: HTMLElement) {
      return this.textContent === "Files" ? 60 : 100;
    },
  );
});
afterEach(() => vi.restoreAllMocks());
afterAll(() => {
  if (descriptor) Object.defineProperty(globalThis, "ResizeObserver", descriptor);
});

describe("TabBar without resize observation", () => {
  it("positions the initial indicator using available layout", () => {
    const view = render(<TabBar tabs={tabs} active="overview" onChange={() => {}} />);

    expect(view.container.querySelector('[aria-hidden="true"]')).toHaveStyle({
      transform: "translateX(0px) scaleX(100)",
    });
  });

  it("follows the newly selected tab", () => {
    const view = render(<TabBar tabs={tabs} active="overview" onChange={() => {}} />);

    view.rerender(<TabBar tabs={tabs} active="files" onChange={() => {}} />);

    expect(view.container.querySelector('[aria-hidden="true"]')).toHaveStyle({
      transform: "translateX(100px) scaleX(60)",
    });
  });
});
