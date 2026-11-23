import { getSessionVersion } from "@/lib/session-transport";

export const OPEN_KEYBOARD_SHORTCUTS = "printstash:keyboard-shortcuts";
export const CLOSE_SEARCH_SUGGESTIONS = "printstash:close-search-suggestions";
let renameRequest: { path: string; session: number; sourceHref: string } | null = null;

export function requestCollectionRename(path: string) {
  renameRequest = {
    path,
    session: getSessionVersion(),
    sourceHref: `${window.location.pathname}${window.location.search}`,
  };
  window.dispatchEvent(new CustomEvent("printstash:rename-collection", { detail: path }));
}

export function pendingCollectionRename() {
  if (renameRequest?.session !== getSessionVersion()) renameRequest = null;
  return renameRequest;
}

export function consumeCollectionRename(path: string): boolean {
  const request = pendingCollectionRename();
  if (!request || request.path !== path) return false;
  renameRequest = null;
  return true;
}

export function cancelCollectionRename() {
  renameRequest = null;
}
export function openKeyboardShortcuts() {
  window.dispatchEvent(new Event(OPEN_KEYBOARD_SHORTCUTS));
}

export function isTypingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      target.closest(
        "input, textarea, select, [role='textbox'], [contenteditable]:not([contenteditable='false'])",
      ) !== null)
  );
}

export function keyboardOverlayOpen(): boolean {
  return (
    modalOverlayOpen() ||
    document.querySelector(
      '[role="dialog"][data-state="open"]:not([data-overlay-open="false"]), [role="menu"][data-state="open"], [role="listbox"][data-state="open"]',
    ) !== null
  );
}

export function modalOverlayOpen(): boolean {
  // Transition state is closed both before entry and during exit. Actual open
  // ownership blocks shortcuts immediately on entry and releases it on close.
  return (
    document.querySelector('dialog[open], [aria-modal="true"]:not([data-overlay-open="false"])') !==
    null
  );
}
