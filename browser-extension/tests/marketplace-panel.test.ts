/** The site launcher keeps transfers alive and accepts dismissal only from its own frame. */
import { fakeBrowser } from "@webext-core/fake-browser";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { marketplaceOrigin, mountMarketplacePanel } from "../marketplace-panel.ts";
import {
  LAUNCHER_CORNERS,
  LAUNCHER_VARIANTS,
  launcherPreferences,
} from "../launcher-preferences.ts";

const frameUrl = "chrome-extension://test/popup.html?panel=1";
const site = "https://www.thingiverse.com/*";
const model = "https://www.thingiverse.com/thing:763622";
const preferences = () => launcherPreferences(fakeBrowser.storage);
const host = () => document.getElementById("printstash-capture-panel")!;

function mount(url = frameUrl, page = model) {
  mountMarketplacePanel(url, preferences(), () => page);
}

beforeEach(() => fakeBrowser.reset());

afterEach(() => {
  window.dispatchEvent(new Event("pagehide"));
  document.body.replaceChildren();
  document.getElementById("printstash-capture-panel")?.remove();
});
describe("marketplace panel", () => {
  it("hides the panel with Escape while retaining the controller", () => {
    mount();
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
    mount();
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
    mount(url);
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
    mount();
    mount();
    expect(document.querySelectorAll("#printstash-capture-panel")).toHaveLength(1);
  });
  it.each(LAUNCHER_VARIANTS)(
    "renders the %s launcher with the PrintStash mark",
    async (variant) => {
      await preferences().setVariant(variant);
      mount();

      await vi.waitFor(() => expect(host().hidden).toBe(false));
      const shell = host().shadowRoot!.querySelector<HTMLElement>(".shell")!;
      expect(shell.dataset.variant).toBe(variant);
      expect(shell.querySelector("img")?.getAttribute("src")).toBe(
        "chrome-extension://test/icon.svg",
      );
      expect(shell.querySelector("button")?.getAttribute("aria-label")).toBe(
        "Import to PrintStash",
      );
    },
  );

  it.each(LAUNCHER_CORNERS)("anchors the %s panel to its launcher shell", async (corner) => {
    await preferences().setCorner(corner);
    mount();

    await vi.waitFor(() => expect(host().hidden).toBe(false));
    const shell = host().shadowRoot!.querySelector<HTMLElement>(".shell")!;
    shell.querySelector("button")!.click();

    expect(shell.dataset.corner).toBe(corner);
    expect(shell.querySelector("iframe")!.hidden).toBe(false);
    expect(shell.querySelector(".launcher-close")?.textContent).toBe("×");
    expect(shell.querySelector("button")?.getAttribute("aria-label")).toBe(
      "Close PrintStash panel",
    );
  });

  it("updates a mounted launcher when its preferences change", async () => {
    mount();
    const shell = host().shadowRoot!.querySelector<HTMLElement>(".shell")!;
    await vi.waitFor(() => expect(shell.dataset.variant).toBe("icon"));

    await preferences().setVariant("edge-tab");
    await preferences().setCorner("bottom-left");

    await vi.waitFor(() => expect(shell.dataset.variant).toBe("edge-tab"));
    expect(shell.dataset.corner).toBe("bottom-left");
    expect(host().hidden).toBe(false);
  });

  it("hides then restores a launcher on its current site without a reload", async () => {
    mount();
    await vi.waitFor(() => expect(host().hidden).toBe(false));

    await preferences().setSiteHidden(site, true);
    await vi.waitFor(() => expect(host().hidden).toBe(true));

    await preferences().setSiteHidden(site, false);
    await vi.waitFor(() => expect(host().hidden).toBe(false));
  });

  it("hides a mounted launcher when the global switch is off", async () => {
    mount();
    await vi.waitFor(() => expect(host().hidden).toBe(false));

    await preferences().setEnabled(false);

    await vi.waitFor(() => expect(host().hidden).toBe(true));
  });

  it("stays hidden on a supported site's home page", async () => {
    mount(frameUrl, "https://www.thingiverse.com/");
    await vi.waitFor(() =>
      expect(host().shadowRoot!.querySelector(".shell")?.getAttribute("data-variant")).toBe("icon"),
    );
    expect(host().hidden).toBe(true);
  });

  it.each([
    "https://makerworld.com.evil.test/",
    "http://makerworld.com/",
    "https://makerworld.com:8443/",
    "javascript:alert(1)",
  ])("rejects unintended site access for %s", (url) => expect(marketplaceOrigin(url)).toBeNull());
});
