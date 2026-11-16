import theme from "./theme.css?inline";
import panelStyle from "./marketplace-panel.css?inline";

import { MARKETPLACE_ORIGINS } from "./source-providers.ts";
export { MARKETPLACE_ORIGINS } from "./source-providers.ts";
export function marketplaceOrigin(url: string): string | null {
  try {
    const origin = `${new URL(url).origin}/*`;
    return MARKETPLACE_ORIGINS.includes(origin) ? origin : null;
  } catch {
    return null;
  }
}
/** Hide the iframe without destroying its extension-owned transfer controller. */
export function mountMarketplacePanel(url: string): void {
  if (document.getElementById("printstash-capture-panel")) return;
  const host = document.createElement("div");
  host.id = "printstash-capture-panel";
  // The shadow tree isolates styles; the extension-origin iframe is the trust boundary.
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = theme + panelStyle;
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Import to PrintStash";
  button.setAttribute("aria-label", "Open PrintStash import menu");
  button.setAttribute("aria-controls", "printstash-import-frame");
  button.setAttribute("aria-expanded", "false");
  const frame = document.createElement("iframe");
  frame.id = "printstash-import-frame";
  frame.title = "PrintStash import menu";
  frame.hidden = true;
  const setOpen = (open: boolean) => {
    // Loading lazily avoids inspecting a page until the user opens the panel.
    if (open && !frame.src) frame.src = url;
    frame.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute(
      "aria-label",
      open ? "Hide PrintStash import menu" : "Open PrintStash import menu",
    );
    button.textContent = open ? "Hide PrintStash" : "Import to PrintStash";
    if (!open) button.focus();
  };
  button.addEventListener("click", () => setOpen(frame.hidden === true));
  shadow.addEventListener("keydown", (event) => {
    if (event instanceof KeyboardEvent && event.key === "Escape" && !frame.hidden) {
      event.preventDefault();
      setOpen(false);
    }
  });
  const extension = new URL(url);
  window.addEventListener("message", (event: MessageEvent) => {
    // A page cannot impersonate this extension frame.
    if (
      event.source !== frame.contentWindow ||
      event.origin !== `${extension.protocol}//${extension.host}`
    )
      return;
    if (event.data?.type === "printstash-hide-panel") setOpen(false);
  });
  shadow.append(style, frame, button);
  document.documentElement.append(host);
}
