"use client";

import {
  Bookmark,
  FolderClosed,
  Inbox,
  Keyboard,
  LogIn,
  Printer,
  Search,
  SlidersHorizontal,
  Star,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { useId, useState, useSyncExternalStore } from "react";

import { DropdownMenu } from "@/components/ui/dropdown-menu";
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

type RailDestination =
  | "library"
  | "search"
  | "inbox"
  | "favorites"
  | "printers"
  | "profiles"
  | "trash";
type NavItem = {
  id: RailDestination;
  href: string;
  labelKey: MessageKey;
  icon: LucideIcon;
  adminOnly?: boolean;
};

const mainItems: NavItem[] = [
  { id: "library", href: "/", labelKey: "Collections", icon: FolderClosed },
  { id: "search", href: "/search", labelKey: "Search", icon: Search },
  { id: "inbox", href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { id: "favorites", href: "/?favorites=true", labelKey: "Favorites", icon: Star },
];

const utilityItems: NavItem[] = [
  { id: "printers", href: "/printers", labelKey: "nav.printers", icon: Printer, adminOnly: true },
  { id: "profiles", href: "/profiles", labelKey: "nav.profiles", icon: SlidersHorizontal },
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

function RailMark() {
  const clipId = useId();
  return (
    <span className="flex h-7.5 w-7.5 items-center justify-center rounded-brand bg-primary text-primary-foreground">
      <svg viewBox="0 0 30 30" className="h-7.5 w-7.5" fill="none" aria-hidden>
        <clipPath id={clipId}>
          <path
            d="M10 6h6.6a5.2 5.2 0 0 1 0 10.4h-3V24H10zM13.6 9.4V13h2.9a1.8 1.8 0 0 0 0-3.6z"
            fillRule="evenodd"
            clipRule="evenodd"
          />
        </clipPath>
        <g clipPath={`url(#${clipId})`} fill="currentColor">
          {[6, 9, 12, 15, 18, 21].map((y) => (
            <rect key={y} x="8" y={y} width="16" height={y === 21 ? 3 : 2.2} />
          ))}
        </g>
      </svg>
    </span>
  );
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
  const favorites = pathname === "/" && params.get("favorites") === "true";

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
          !favorites &&
          (pathname === "/" || /^\/(models|multipart-models|documents)\//.test(pathname))
        );
      case "search":
        if (pane?.open) return pane.mode === "search";
        return pathname.startsWith("/search");
      case "inbox":
        if (pane?.open && pane.mode === "pending") return true;
        return pathname.startsWith("/inbox");
      case "favorites":
        if (pane?.open && pane.mode !== "favorites") return false;
        return favorites && !savedViewsOpen;
      case "printers":
        return pathname.startsWith("/printers");
      case "profiles":
        return pathname.startsWith("/profiles");
      case "trash":
        return (
          pathname === "/settings" &&
          params.get("section") === "trash" &&
          params.get("view") !== "settings"
        );
    }
  }

  const railClass = (active: boolean) =>
    `relative flex h-10 w-10 shrink-0 items-center justify-center transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${
      active
        ? "bg-accent text-accent-foreground before:absolute before:-left-1.5 before:bottom-[9px] before:top-[9px] before:w-[3px] before:bg-primary"
        : "text-muted-foreground hover:bg-muted hover:text-foreground"
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
          active && item.id !== "library" && item.id !== "search" && item.id !== "inbox"
            ? "page"
            : undefined
        }
        aria-expanded={
          item.id === "library" || item.id === "search" || item.id === "inbox"
            ? active && pane?.open
            : undefined
        }
        aria-controls={
          item.id === "library" || item.id === "search" || item.id === "inbox"
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
          if (pane && (item.id === "library" || item.id === "search" || item.id === "inbox")) {
            const mode =
              item.id === "library" ? "collections" : item.id === "search" ? "search" : "pending";
            if (pane.open && pane.mode === mode) pane.toggle();
            else if (mode === "collections") pane.showCollections();
            else if (mode === "search") pane.showSearch();
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
          <RailMark />
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
                    if (pathname !== "/") router.push("/");
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
                  className="mt-1 flex h-10 w-10 items-center justify-center hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
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
