import { marketplacePanelActivation, restoreMarketplacePanels } from "../panel-registration.ts";
import { defineBackground } from "wxt/utils/define-background";
import { browser } from "wxt/browser";
import { marketplaceOrigin } from "../marketplace-panel.ts";

export default defineBackground(() => {
  const panelApi = {
    storage: browser.storage.local,
    permissions: browser.permissions,
    scripting: browser.scripting,
  };
  const activation = marketplacePanelActivation(
    panelApi,
    browser.storage.session,
    async (origin) => {
      const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
      if (tab?.id && tab.url && marketplaceOrigin(tab.url) === origin) {
        await browser.scripting.executeScript({
          target: { tabId: tab.id },
          files: ["/marketplace.js"],
        });
      }
    },
  );
  browser.permissions.onAdded.addListener(({ origins }) => {
    void activation
      .permissionAdded(origins ?? [])
      .catch(() => console.warn("PrintStash launcher could not be activated."));
  });
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
    void activation.request(origin).then(sendResponse, () => sendResponse(false));
    return true;
  });
});
