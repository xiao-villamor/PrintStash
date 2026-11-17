/** The site launcher keeps transfers alive and accepts dismissal only from its own frame. */
import { afterEach, describe, expect, it } from "vitest";
import { marketplaceOrigin, mountMarketplacePanel } from "../marketplace-panel.ts";
afterEach(() => {
  document.body.replaceChildren();
  document.getElementById("printstash-capture-panel")?.remove();
});
describe("marketplace panel", () => {
  it("hides the panel with Escape while retaining the controller", () => {
    mountMarketplacePanel("chrome-extension://test/popup.html?panel=1");
    const root = document.getElementById("printstash-capture-panel")!.shadowRoot!;
    const button = root.querySelector("button")!;
    const frame = root.querySelector("iframe")!;
    button.click();
    button.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(frame.hidden).toBe(true);
    expect(frame.isConnected).toBe(true);
    expect(root.activeElement).toBe(button);
  });
  it("ignores dismissal from the marketplace window", () => {
    mountMarketplacePanel("chrome-extension://test/popup.html?panel=1");
    const root = document.getElementById("printstash-capture-panel")!.shadowRoot!;
    root.querySelector("button")!.click();
    window.dispatchEvent(
      new MessageEvent("message", {
        source: window,
        origin: "https://makerworld.com",
        data: { type: "printstash-hide-panel" },
      }),
    );
    expect(root.querySelector("iframe")!.hidden).toBe(false);
  });

  it("keeps the same extension iframe alive while hidden", () => {
    const url = "chrome-extension://test/popup.html?panel=1";
    mountMarketplacePanel(url);
    const root = document.getElementById("printstash-capture-panel")?.shadowRoot;
    const button = root?.querySelector("button");
    const frame = root?.querySelector("iframe");
    expect(frame?.hasAttribute("src")).toBe(false);
    button?.click();
    expect(frame?.src).toBe(url);
    expect(frame?.hidden).toBe(false);
    button?.click();
    expect(frame?.hidden).toBe(true);
    expect(frame?.isConnected).toBe(true);
    button?.click();
    expect(root?.querySelector("iframe")).toBe(frame);
    expect(button?.getAttribute("aria-expanded")).toBe("true");
  });
  it("does not mount duplicate controls", () => {
    mountMarketplacePanel("chrome-extension://test/popup.html?panel=1");
    mountMarketplacePanel("chrome-extension://test/popup.html?panel=1");
    expect(document.querySelectorAll("#printstash-capture-panel")).toHaveLength(1);
  });
  it.each([
    "https://makerworld.com.evil.test/",
    "http://makerworld.com/",
    "https://makerworld.com:8443/",
    "javascript:alert(1)",
  ])("rejects unintended site access for %s", (url) => expect(marketplaceOrigin(url)).toBeNull());
});
