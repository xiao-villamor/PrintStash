import { defineUnlistedScript } from "wxt/utils/define-unlisted-script";
import { browser } from "wxt/browser";
import { launcherPreferences } from "../launcher-preferences.ts";
import { mountMarketplacePanel } from "../marketplace-panel.ts";

export default defineUnlistedScript(() => {
  mountMarketplacePanel(
    `${browser.runtime.getURL("/popup.html")}?panel=1`,
    launcherPreferences(browser.storage),
  );
});
