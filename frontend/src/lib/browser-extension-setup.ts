export const BROWSER_EXTENSION_SETUP_STORAGE_KEY = "printstash.browser-extension-setup:v1";

export const BROWSER_EXTENSION_SETUP_TTL_MS = 5 * 60 * 1000;

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
  return setup;
}
