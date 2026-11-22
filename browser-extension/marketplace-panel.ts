import theme from "./theme.css?inline";
import launcherStyle from "./launcher.css?inline";
import panelStyle from "./marketplace-panel.css?inline";
import { launcherButton, positionLauncher } from "./launcher.ts";
import {
  defaultLauncherPreferences,
  type LauncherPreferencesOwner,
} from "./launcher-preferences.ts";

import { MARKETPLACE_ORIGINS, sourcePublication } from "./source-providers.ts";
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
export function mountMarketplacePanel(
  url: string,
  preferences: LauncherPreferencesOwner,
  currentPage = () => location.href,
): void {
  if (document.getElementById("printstash-capture-panel")) return;
  const host = document.createElement("div");
  host.id = "printstash-capture-panel";
  host.hidden = true;
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = theme + launcherStyle + panelStyle;
  const shell = document.createElement("div");
  shell.className = "shell";
  const button = launcherButton(new URL("/icon.svg", url).href);
  button.setAttribute("aria-controls", "printstash-import-frame");
  button.setAttribute("aria-expanded", "false");
  const frame = document.createElement("iframe");
  frame.id = "printstash-import-frame";
  frame.title = "PrintStash import panel";
  frame.hidden = true;
  const setOpen = (open: boolean, restoreFocus = true) => {
    if (open && !frame.hasAttribute("src")) frame.src = url;
    frame.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", open ? "Close PrintStash panel" : "Import to PrintStash");
    if (!open && restoreFocus) button.focus();
  };
  button.addEventListener("click", () => setOpen(frame.hidden === true));
  shadow.addEventListener("keydown", (event) => {
    if (event instanceof KeyboardEvent && event.key === "Escape" && !frame.hidden) {
      event.preventDefault();
      setOpen(false);
    }
  });

  const extension = new URL(url);
  const listeners = new AbortController();
  window.addEventListener(
    "message",
    (event: MessageEvent) => {
      // The page cannot impersonate the extension frame.
      if (
        event.source !== frame.contentWindow ||
        event.origin !== `${extension.protocol}//${extension.host}`
      )
        return;
      if (event.data?.type === "printstash-hide-panel") setOpen(false);
      if (
        event.data?.type === "printstash-panel-height" &&
        Number.isSafeInteger(event.data.height) &&
        event.data.height > 0
      ) {
        frame.style.setProperty("--panel-content-height", `${event.data.height + 2}px`);
      }
    },
    { signal: listeners.signal },
  );

  let settings = defaultLauncherPreferences();
  function apply() {
    const page = currentPage();
    const origin = marketplaceOrigin(page);
    host.hidden =
      !settings.enabled || !origin || !sourcePublication(page) || settings.hiddenSites.has(origin);
    if (host.hidden) setOpen(false, false);
    positionLauncher(shell, settings.corner, settings.variant);
  }
  const unsubscribe = preferences.subscribe((value) => {
    settings = value;
    apply();
  }, apply);
  window.navigation?.addEventListener("currententrychange", apply, { signal: listeners.signal });
  window.addEventListener("popstate", apply, { signal: listeners.signal });
  window.addEventListener(
    "pagehide",
    (event) => {
      if (event.persisted) return;
      listeners.abort();
      unsubscribe();
    },
    { signal: listeners.signal },
  );

  shell.append(frame, button);
  shadow.append(style, shell);
  document.documentElement.append(host);
}
