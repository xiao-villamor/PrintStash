import { registerMarketplacePanel, restoreMarketplacePanels } from "../panel-registration.ts";
import { defineBackground } from "wxt/utils/define-background";
import { browser } from "wxt/browser";
import { marketplaceOrigin } from "../marketplace-panel.ts";

export default defineBackground(() => {
  const panelApi = {
    storage: browser.storage.local,
    permissions: browser.permissions,
    scripting: browser.scripting,
  };
  void restoreMarketplacePanels(panelApi).catch(() =>
    console.warn("PrintStash site controls could not be restored."),
  );
  browser.permissions.onRemoved.addListener(() => {
    void restoreMarketplacePanels(panelApi).catch(() =>
      console.warn("PrintStash site controls could not be refreshed."),
    );
  });
  browser.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
    if (
      sender.id !== browser.runtime.id ||
      typeof message !== "object" ||
      message === null ||
      !("type" in message)
    )
      return false;
    if (message.type === "printstash-panel-context") {
      if (sender.tab?.url && marketplaceOrigin(sender.tab.url)) {
        sendResponse({ id: sender.tab.id, url: sender.tab.url, title: sender.tab.title });
      } else sendResponse(null);
      return false;
    }
    if (
      message.type !== "printstash-enable-panel" ||
      !sender.url?.startsWith(browser.runtime.getURL("/popup.html")) ||
      !("origin" in message) ||
      typeof message.origin !== "string"
    )
      return false;
    const origin = marketplaceOrigin(message.origin.replace(/\*$/, ""));
    if (!origin) return false;
    void (async () => {
      if (!(await registerMarketplacePanel(panelApi, origin))) {
        sendResponse(false);
        return;
      }
      const tabs = await browser.tabs.query({ active: true, currentWindow: true });
      const tab = tabs[0];
      if (tab?.id && tab.url && marketplaceOrigin(tab.url) === origin)
        await browser.scripting.executeScript({
          target: { tabId: tab.id },
          files: ["/marketplace.js"],
        });
      sendResponse(true);
    })().catch(() => sendResponse(false));
    return true;
  });
});
