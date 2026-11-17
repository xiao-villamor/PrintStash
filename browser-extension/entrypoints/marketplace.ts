import { defineUnlistedScript } from "wxt/utils/define-unlisted-script";
import { browser } from "wxt/browser";
import { mountMarketplacePanel } from "../marketplace-panel.ts";

export default defineUnlistedScript(() => {
  mountMarketplacePanel(`${browser.runtime.getURL("/popup.html")}?panel=1`);
});
