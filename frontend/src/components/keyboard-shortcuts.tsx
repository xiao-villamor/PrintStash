import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { ModalShell } from "@/components/ui/modal";
import { useCollectionsPane } from "@/lib/collections-pane-context";
import { useMediaQuery } from "@/lib/use-media-query";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import { useUiLocale } from "@/lib/i18n";
import { uiText, type MessageKey } from "@/lib/locale";
import { undoLastChange } from "@/lib/toast";
import {
  cancelCollectionRename,
  CLOSE_SEARCH_SUGGESTIONS,
  isTypingTarget,
  keyboardOverlayOpen,
  modalOverlayOpen,
  OPEN_KEYBOARD_SHORTCUTS,
  OUTLINER_KEYBOARD_ROW,
  pendingCollectionRename,
  requestCollectionRename,
} from "@/lib/keyboard-commands";

type Shortcut = { labelKey: MessageKey; keys: string[] };
export function KeyboardShortcuts() {
  useUiLocale();
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const pane = useCollectionsPane();
  const desktop = useMediaQuery("(min-width: 768px)");
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const [focusRequest, requestFocus] = useState<{ token: number; kind: "search" | "collections" }>({
    token: 0,
    kind: "collections",
  });
  const handledFocus = useRef(0);
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  const mod = mac ? "⌘" : "Ctrl";
  const shift = mac ? "⇧" : "Shift";
  const alt = mac ? "⌥" : "Alt";
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_KEYBOARD_SHORTCUTS, show);
    return () => window.removeEventListener(OPEN_KEYBOARD_SHORTCUTS, show);
  }, []);
  useEffect(() => {
    const pending = pendingCollectionRename();
    if (
      pending &&
      `${pathname}${window.location.search}` !== pending.sourceHref &&
      (pathname !== "/" || params.get("c") !== pending.path)
    )
      cancelCollectionRename();
  }, [pathname, params]);
  useEffect(() => {
    if (
      !focusRequest.token ||
      handledFocus.current === focusRequest.token ||
      !pane?.open ||
      pane.mode !== focusRequest.kind
    )
      return;
    const host = document.querySelector<HTMLElement>("[data-collections-pane]");
    if (!host) return;
    let observer: MutationObserver | null = null;
    let cancelled = false;
    const stop = () => {
      cancelled = true;
      observer?.disconnect();
      handledFocus.current = focusRequest.token;
    };
    function focus() {
      if (cancelled || !host) return;
      const target =
        focusRequest.kind === "search"
          ? host.querySelector<HTMLElement>("[data-shell-panel-search]")
          : (host.querySelector<HTMLElement>('[data-outliner-collection][aria-selected="true"]') ??
            host.querySelector<HTMLElement>("[data-outliner-match]") ??
            host.querySelector<HTMLElement>(OUTLINER_KEYBOARD_ROW));
      if (target) {
        target.focus({ preventScroll: focusRequest.kind === "search" });
        if (document.activeElement === target) stop();
      }
    }
    observer = new MutationObserver(focus);
    observer.observe(host, { childList: true, subtree: true });
    host.addEventListener("input", stop, true);
    const frame = requestAnimationFrame(focus);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      host.removeEventListener("input", stop, true);
    };
  }, [focusRequest, pane?.open, pane?.mode]);
  useEffect(() => {
    function focusSearch() {
      const folderSearch = Array.from(
        document.querySelectorAll<HTMLInputElement>("[data-library-search]"),
      ).find((element) => element.getClientRects().length > 0);
      if (folderSearch) {
        folderSearch.focus();
        folderSearch.select();
        return;
      }
      if (desktop && pane) {
        pane.showSearch();
        requestFocus((value) => ({ token: value.token + 1, kind: "search" }));
      } else {
        const input = Array.from(
          document.querySelectorAll<HTMLInputElement>("[data-model-search]"),
        ).find((element) => element.getClientRects().length > 0);
        if (input) input.focus();
        else router.push("/search");
      }
    }
    function keydown(event: KeyboardEvent) {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.repeat ||
        event.getModifierState("AltGraph")
      )
        return;
      const modified = event.metaKey || event.ctrlKey;
      const typing = isTypingTarget(event.target);
      if (open && !typing && !modified && !event.altKey && event.key === "?") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (modalOverlayOpen()) return;
      if (modified && !event.altKey && pane) {
        if (event.code === "KeyB" && !event.shiftKey) {
          event.preventDefault();
          window.dispatchEvent(new Event(CLOSE_SEARCH_SUGGESTIONS));
          if (pane.open && document.activeElement?.closest("[data-collections-pane]"))
            document
              .querySelector<HTMLButtonElement>('header [aria-controls="collections-pane"]')
              ?.focus({ preventScroll: true });
          if (desktop && !pane.open && pane.mode === "collections")
            requestFocus((value) => ({ token: value.token + 1, kind: "collections" }));
          pane.toggle();
          return;
        }
        if (event.code === "KeyE" && event.shiftKey) {
          event.preventDefault();
          window.dispatchEvent(new Event(CLOSE_SEARCH_SUGGESTIONS));
          pane.showCollections();
          requestFocus((value) => ({ token: value.token + 1, kind: "collections" }));
          return;
        }
        if (event.code === "KeyF" && event.shiftKey) {
          event.preventDefault();
          window.dispatchEvent(new Event(CLOSE_SEARCH_SUGGESTIONS));
          focusSearch();
          return;
        }
        if (event.code === "KeyZ" && !event.shiftKey && !typing && undoLastChange()) {
          event.preventDefault();
          return;
        }
      }
      if (keyboardOverlayOpen()) return;
      if (typing || modified || event.altKey) return;
      if (event.key === "/") {
        event.preventDefault();
        focusSearch();
        return;
      }
      if (event.key === "?") {
        event.preventDefault();
        setOpen(true);
        return;
      }
      if (
        event.key === "Escape" &&
        /^\/(models|multipart-models|documents)\/\d+\/?$/.test(pathname)
      ) {
        const back = document.querySelector<HTMLAnchorElement>("header [data-shell-back]");
        if (back) {
          event.preventDefault();
          back.click();
        }
        return;
      }
      if (event.key === "F2") {
        const target = event.target instanceof HTMLElement ? event.target : null;
        if (target?.closest("article, tr, #model-drawer, [data-outliner-entry]")) return;
        const path = document.querySelector<HTMLElement>("header[data-current-collection]")?.dataset
          .currentCollection;
        if (!path) return;
        event.preventDefault();
        requestCollectionRename(path);
        if (pathname !== "/") {
          const back = document.querySelector<HTMLAnchorElement>("header [data-shell-back]");
          if (back && new URL(back.href).searchParams.get("c") === path) back.click();
          else router.push(`/?${new URLSearchParams({ c: path })}`);
        }
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [open, pane, pathname, router, desktop]);
  const groups: { titleKey: MessageKey; noteKey?: MessageKey; rows: Shortcut[] }[] = [
    {
      titleKey: "Find and move around",
      rows: [
        { labelKey: "Search", keys: ["/"] },
        { labelKey: "Search panel", keys: [mod, shift, "F"] },
        { labelKey: "Find in collection", keys: [mod, "F"] },
        { labelKey: "Collections panel", keys: [mod, shift, "E"] },
        { labelKey: "Show or hide the sidebar", keys: [mod, "B"] },
        { labelKey: "Show or hide details", keys: [mod, alt, "B"] },
        { labelKey: "Move in the collection tree", keys: ["↑", "↓", "←", "→"] },
        { labelKey: "Focus Library results", keys: ["G"] },
        { labelKey: "Move between Library results", keys: ["↑", "↓", "←", "→"] },
      ],
    },
    {
      titleKey: "In the find bar",
      noteKey: "With the find bar open",
      rows: [
        { labelKey: "Next match", keys: ["Enter"] },
        { labelKey: "Previous match", keys: [shift, "Enter"] },
        { labelKey: "Close the find bar", keys: ["Esc"] },
      ],
    },
    {
      titleKey: "Models",
      rows: [
        { labelKey: "Close details, then clear the selection", keys: ["Esc"] },
        { labelKey: "Open the actions menu", keys: [shift, "F10"] },
        { labelKey: "Rename this collection", keys: ["F2"] },
        { labelKey: "Undo the last change", keys: [mod, "Z"] },
        { labelKey: "Show this list", keys: ["?"] },
      ],
    },
    {
      titleKey: "Print results",
      noteKey: "With focus in a model's G-code revisions",
      rows: [
        { labelKey: "Choose a revision", keys: ["↑", "↓"] },
        { labelKey: "Log a print that came out OK", keys: ["1"] },
        { labelKey: "Log a failed print, with a note", keys: ["2"] },
        { labelKey: "Save the failure note", keys: [mod, "Enter"] },
      ],
    },
  ];
  return (
    <ModalShell
      open={open}
      onClose={() => setOpen(false)}
      labelledBy={titleId}
      className="keyboard-help flex max-h-[calc(100dvh-48px)] w-160 max-w-[calc(100vw-48px)] flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border pb-5 pl-6 pr-4.5 pt-6">
        <h2 id={titleId} className="min-w-0 flex-1 text-lg font-semibold leading-snug">
          {uiText("Keyboard shortcuts")}
        </h2>
        <button
          type="button"
          aria-label={uiText("Close keyboard shortcuts")}
          onClick={() => setOpen(false)}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div className="keyboard-help-grid min-h-0 overflow-y-auto px-6 pb-6 pt-2">
        {groups.map(({ titleKey, noteKey, rows }) => (
          <section key={titleKey} className="min-w-0">
            <h3 className="mb-1.5 pt-4 text-2xs font-semibold uppercase tracking-wider text-muted-foreground">
              {uiText(titleKey)}
            </h3>
            {noteKey && <p className="mb-1.5 text-xs text-muted-foreground">{uiText(noteKey)}</p>}
            <dl>
              {rows.map(({ labelKey, keys }) => (
                <div
                  key={labelKey}
                  className="flex min-h-8 items-center gap-3 border-b border-border py-1.25"
                >
                  <dt className="min-w-0 flex-1 text-[13px] leading-[1.4]">{uiText(labelKey)}</dt>
                  <dd className="m-0 flex shrink-0 gap-1">
                    {keys.map((key, index) => (
                      <kbd
                        key={`${index}:${key}`}
                        className="h-6 min-w-6 rounded-md border border-border border-b-2 bg-muted px-1.5 text-center font-mono text-xs leading-[21px] text-foreground"
                      >
                        {key}
                      </kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </ModalShell>
  );
}
