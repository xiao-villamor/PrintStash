import { onAuthChange } from "@/lib/auth-store";

export const BROWSER_EXTENSION_SETUP_STORAGE_KEY = "printstash.browser-extension-setup:v1";

export const BROWSER_EXTENSION_SETUP_TTL_MS = 5 * 60 * 1000;

let releaseRetirement: (() => void) | null = null;
let ownedSetup: BrowserExtensionSetup | null = null;

export interface BrowserExtensionSetup {
  version: 1;
  vault: string;
  username: string;
  apiKey: string;
  expiresAt: number;
}

export function prepareBrowserExtensionSetup(
  vault: string,
  username: string,
  apiKey: string,
  now = Date.now(),
): BrowserExtensionSetup {
  const parsedVault = new URL(vault);
  const cleanUsername = username.trim();
  const cleanApiKey = apiKey.trim();
  if (!["http:", "https:"].includes(parsedVault.protocol)) {
    throw new Error("Browser extension setup requires an HTTP or HTTPS vault URL.");
  }
  if (!cleanUsername || !cleanApiKey) {
    throw new Error("Browser extension setup requires a username and API key.");
  }

  const setup: BrowserExtensionSetup = {
    version: 1,
    vault: parsedVault.origin,
    username: cleanUsername,
    apiKey: cleanApiKey,
    expiresAt: now + BROWSER_EXTENSION_SETUP_TTL_MS,
  };
  window.sessionStorage.setItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY, JSON.stringify(setup));
  releaseRetirement?.();
  // Handoff may outlive the Settings view, but never the private session that issued it.
  ownedSetup = setup;
  releaseRetirement = onAuthChange(() => discardBrowserExtensionSetup(setup));
  return setup;
}

/** A stale receipt must not discard a newer same-session handoff. */
export function discardBrowserExtensionSetup(setup: BrowserExtensionSetup): void {
  if (
    window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY) === JSON.stringify(setup)
  ) {
    window.sessionStorage.removeItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY);
  }
  if (ownedSetup === setup) {
    releaseRetirement?.();
    releaseRetirement = null;
    ownedSetup = null;
  }
}
