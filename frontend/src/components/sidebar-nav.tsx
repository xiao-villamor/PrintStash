"use client";

import {
  Bookmark,
  FolderClosed,
  Inbox,
  Keyboard,
  LogIn,
  Search,
  FileSliders,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { useState, useSyncExternalStore } from "react";

import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { BrandMark } from "@/components/brand-mark";
import { WorkspaceMenu } from "@/components/workspace-menu";
import { useAuth } from "@/lib/auth-context";
import { useI18n, useUiLocale, type MessageKey } from "@/lib/i18n";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { lastVaultHref } from "@/lib/last-collection";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import { useNavigationShell } from "@/lib/navigation-shell";
import { useInboxCount } from "@/lib/queries/inbox";
import { openKeyboardShortcuts } from "@/lib/keyboard-commands";
import { useCollectionsPane } from "@/lib/collections-pane-context";

type RailDestination = "library" | "search" | "inbox" | "printers" | "profiles" | "trash";
type NavItem = {
  id: RailDestination;
  href: string;
  labelKey: MessageKey;
  icon: LucideIcon;
  adminOnly?: boolean;
};

const mainItems: NavItem[] = [
  { id: "library", href: "/", labelKey: "Library", icon: FolderClosed },
  { id: "search", href: "/search", labelKey: "Search", icon: Search },
  { id: "inbox", href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
];

const utilityItems: NavItem[] = [
  { id: "printers", href: "/printers", labelKey: "nav.printers", icon: Printer3D, adminOnly: true },
  { id: "profiles", href: "/profiles", labelKey: "nav.profiles", icon: FileSliders },
  {
    id: "trash",
    href: "/settings?section=trash",
    labelKey: "settings.trash",
    icon: Trash2,
    adminOnly: true,
  },
];

function subscribeLastVaultHref(onStoreChange: () => void): () => void {
  window.addEventListener("storage", onStoreChange);
  return () => window.removeEventListener("storage", onStoreChange);
}

/** Compact desktop navigation; the mobile navigation owns the same destinations on phones. */
export function SidebarNav() {
  useUiLocale();
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const startup = useLibraryStartup();
  const pane = useCollectionsPane();
  const { requestSavedViews, savedViewsOpen, savedViewsAvailable, attachSavedViews } =
    useNavigationShell();
  const inbox = useInboxCount(user !== null && startup.canLoad("inbox"));
  const pendingCount = user ? (inbox.data ?? 0) : 0;
  const homeHref = useSyncExternalStore(subscribeLastVaultHref, lastVaultHref);
  const [accountOpen, setAccountOpen] = useState(false);

  async function handleLogout() {
    await logout();
    setAccountOpen(false);
    router.push("/login");
  }

  function isActive(id: RailDestination) {
    switch (id) {
      case "library":
        if (pane?.open) return pane.mode === "collections" && !savedViewsOpen;
        return (
          !savedViewsOpen &&
          (pathname === "/" || /^\/(models|multipart-models|documents)\//.test(pathname))
        );
      case "search":
        if (pane?.open) return pane.mode === "search";
        return pathname.startsWith("/search");
      case "inbox":
        if (pane?.open && pane.mode === "pending") return true;
        return pathname.startsWith("/inbox");
      case "printers":
        return pathname.startsWith("/printers");
      case "profiles":
        return pathname.startsWith("/profiles");
      case "trash":
        return pathname === "/settings" && params.get("section") === "trash";
    }
  }

  const railClass = (active: boolean) =>
    `relative flex h-10 w-10 shrink-0 items-center justify-center rounded-md transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${
      active
        ? "bg-accent text-accent-foreground before:absolute before:-left-1.5 before:bottom-[9px] before:top-[9px] before:w-[3px] before:bg-primary"
        : "text-muted-foreground hover:bg-foreground/[0.08] hover:text-foreground"
    }`;

  function itemLink(item: NavItem) {
    if (item.adminOnly && !user?.is_superuser) return null;
    const label = t(item.labelKey);
    const active = isActive(item.id);
    return (
      <a
        key={item.id}
        href={item.href}
        title={label}
        aria-current={
          item.id === "search"
            ? pathname === "/search"
              ? "page"
              : undefined
            : active && item.id !== "library" && item.id !== "inbox"
              ? "page"
              : undefined
        }
        aria-expanded={
          item.id === "library" || item.id === "inbox" || item.id === "search"
            ? active && pane?.open
            : undefined
        }
        aria-controls={
          item.id === "library" || item.id === "inbox" || item.id === "search"
            ? "collections-pane"
            : undefined
        }
        className={railClass(active)}
        onClick={(event) => {
          const target = event.currentTarget.getAttribute("target");
          if (
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey ||
            (target && target !== "_self") ||
            event.currentTarget.hasAttribute("download")
          )
            return;
          event.preventDefault();
          if (item.id === "search") {
            if (pane) pane.showSearch();
            else router.push("/");
            return;
          }
          if (pane && (item.id === "library" || item.id === "inbox")) {
            const mode = item.id === "library" ? "collections" : "pending";
            if (pane.open && pane.mode === mode) pane.toggle();
            else if (mode === "collections") pane.showCollections();
            else pane.showPending();
            return;
          }
          router.push(item.href);
        }}
      >
        <item.icon className="h-5 w-5" strokeWidth={1.6} aria-hidden />
        <span className="sr-only">{label}</span>
        {item.id === "inbox" && pendingCount > 0 && (
          <span className="absolute right-[3px] top-[3px] min-w-[18px] rounded-full bg-secondary px-1 text-center font-mono text-2xs font-semibold leading-[18px] text-secondary-foreground">
            {pendingCount}
          </span>
        )}
      </a>
    );
  }

  return (
    <>
      <nav
        aria-label={uiText("Navigation")}
        className="hidden h-full w-rail shrink-0 flex-col items-center gap-1 border-r border-border bg-rail py-2.5 md:flex"
      >
        <Link
          href={homeHref}
          aria-label="PrintStash"
          className="mb-2.5 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <BrandMark className="size-7.5" />
        </Link>
        <div className="flex min-h-0 flex-1 flex-col items-center gap-1">
          {mainItems.map(itemLink)}
          <div ref={attachSavedViews}>
            {!savedViewsAvailable && (
              <button
                type="button"
                title={uiText("Saved views")}
                aria-label={uiText("Saved views")}
                aria-pressed={savedViewsOpen}
                className={railClass(savedViewsOpen)}
                onClick={() => {
                  if (pane) {
                    if (pane.open && pane.mode === "views") pane.toggle();
                    else pane.showSavedViews();
                  } else {
                    requestSavedViews();
                    const next = new URLSearchParams(pathname === "/" ? params : "");
                    next.delete("c");
                    next.delete("cid");
                    next.delete("scope");
                    router.push(next.size ? `/?${next}` : "/");
                  }
                }}
              >
                <Bookmark className="h-5 w-5" strokeWidth={1.6} aria-hidden />
              </button>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-center gap-1">
          {utilityItems.map(itemLink)}
          <button
            type="button"
            title={uiText("Keyboard shortcuts")}
            aria-label={uiText("Keyboard shortcuts")}
            className={railClass(false)}
            onClick={openKeyboardShortcuts}
          >
            <Keyboard className="h-5 w-5" strokeWidth={1.6} aria-hidden />
          </button>
          {user ? (
            <DropdownMenu
              open={accountOpen}
              onOpenChange={setAccountOpen}
              align="start"
              role="menu"
              portal
              contentClassName="desktop-workspace-popover"
              trigger={
                <button
                  type="button"
                  data-menu-trigger
                  aria-label={t("nav.myWorkspace")}
                  aria-haspopup="menu"
                  aria-expanded={accountOpen}
                  onClick={() => setAccountOpen((open) => !open)}
                  className="mt-1 flex h-10 w-10 items-center justify-center rounded-md hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full border border-input bg-accent text-xs font-semibold text-accent-foreground">
                    {user.username.charAt(0).toUpperCase()}
                    <span className="sr-only">{user.username}</span>
                  </span>
                </button>
              }
            >
              <WorkspaceMenu
                pathname={pathname}
                username={user.username}
                printersInQuickAccess
                isAdmin={user.is_superuser}
                onNavigate={() => setAccountOpen(false)}
                onLogout={handleLogout}
              />
            </DropdownMenu>
          ) : (
            <Link
              href="/login"
              title={t("nav.signIn")}
              aria-label={t("nav.signIn")}
              className={railClass(false)}
            >
              <LogIn className="h-5 w-5" strokeWidth={1.6} aria-hidden />
            </Link>
          )}
        </div>
      </nav>
    </>
  );
}
