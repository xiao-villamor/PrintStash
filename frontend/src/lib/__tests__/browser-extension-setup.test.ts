/*
 * Handing the browser extension its credentials, through the page.
 *
 * The setup package crosses from the app to an extension, so it is written
 * same-origin and short-lived: a long-lived package sitting in storage is an API
 * key any script on the page can read, for as long as the tab exists.
 *
 * Incomplete credentials are rejected rather than stored partially. A package
 * missing its key would be picked up by the extension, fail to authenticate, and
 * present as the extension being broken rather than as a setup that never
 * finished.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  BROWSER_EXTENSION_SETUP_TTL_MS,
  prepareBrowserExtensionSetup,
} from "@/lib/browser-extension-setup";

describe("storeExtensionSetupPackage", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("stores a short-lived same-origin setup package for the extension", () => {
    const now = Date.UTC(2026, 7, 21, 12, 0, 0);
    const setup = prepareBrowserExtensionSetup(
      "http://localhost:3000/settings?section=access",
      " owner ",
      " psk_browser ",
      now,
    );

    expect(setup).toEqual({
      version: 1,
      vault: "http://localhost:3000",
      username: "owner",
      apiKey: "psk_browser",
      expiresAt: now + BROWSER_EXTENSION_SETUP_TTL_MS,
    });
    expect(window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY)).toBe(
      JSON.stringify(setup),
    );
  });

  it("rejects incomplete credentials", () => {
    expect(() => prepareBrowserExtensionSetup("https://prints.example.com", "owner", "")).toThrow(
      "requires a username and API key",
    );
    expect(window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY)).toBeNull();
  });
});
