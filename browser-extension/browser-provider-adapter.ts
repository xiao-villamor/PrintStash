/**
 * The browser API boundary used by the popup. Keeping it structural makes the
 * Chrome and Firefox implementations interchangeable and lets fake-browser
 * exercise storage, permissions, tabs, and scripting without a live browser.
 */
export interface BrowserProviderAdapter {
  runtime: {
    getManifest(): { version: string };
  };
  storage: {
    get(keys: string | string[]): Promise<Record<string, unknown>>;
    set(values: Record<string, unknown>): Promise<void>;
    remove(keys: string | string[]): Promise<void>;
  };
  permissions: {
    contains(details: { origins: string[] }): Promise<boolean>;
    request(details: { origins: string[] }): Promise<boolean>;
    remove(details: { origins: string[] }): Promise<boolean>;
  };
  tabs: {
    query(queryInfo: { active: boolean; currentWindow: boolean }): Promise<BrowserTab[]>;
    create(createProperties: { url: string }): Promise<unknown>;
  };
  scripting: {
    executeScript(details: BrowserScriptRequest): Promise<Array<{ result?: unknown }>>;
  };
}

export interface BrowserExtensionApi {
  runtime: BrowserProviderAdapter["runtime"];
  storage: { local: BrowserProviderAdapter["storage"] };
  permissions: BrowserProviderAdapter["permissions"];
  tabs: BrowserProviderAdapter["tabs"];
  scripting: BrowserProviderAdapter["scripting"];
}

export interface BrowserTab {
  id?: number;
  title?: string;
  url?: string;
}

export interface BrowserScriptRequest {
  target: { tabId: number };
  world?: "MAIN" | "ISOLATED";
  func?: (...args: never[]) => unknown;
  args?: unknown[];
}

export function createBrowserProviderAdapter(browser: BrowserExtensionApi): BrowserProviderAdapter {
  return {
    runtime: browser.runtime,
    storage: browser.storage.local,
    permissions: browser.permissions,
    tabs: browser.tabs,
    scripting: browser.scripting,
  };
}
