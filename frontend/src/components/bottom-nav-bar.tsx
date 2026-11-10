"use client";

import { useState } from "react";
import {
  FolderClosed,
  Inbox,
  MoreHorizontal,
  Printer,
  Search,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { WorkspaceMenu } from "@/components/workspace-menu";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import { useAuth } from "@/lib/auth-context";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useInboxCount } from "@/lib/queries/inbox";

type NavItem = { href: string; labelKey: MessageKey; icon: LucideIcon };

export function BottomNavBar() {
  const { t } = useI18n();
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const { user, logout } = useAuth();
  const startup = useLibraryStartup();
  const inbox = useInboxCount(startup.canLoad("inbox"));
  const [openedOnPath, setOpenedOnPath] = useState<string | null>(null);
  const moreOpen = openedOnPath === pathname;
  const tabs: NavItem[] = [
    { href: "/", labelKey: "Library", icon: FolderClosed },
    { href: "/search", labelKey: "Search", icon: Search },
    { href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
    user?.is_superuser
      ? { href: "/printers", labelKey: "nav.printers", icon: Printer }
      : { href: "/profiles", labelKey: "nav.profiles", icon: SlidersHorizontal },
  ];
  const moreActive =
    [
      "/statistics",
      "/settings",
      ...(user?.is_superuser ? ["/profiles"] : []),
      "/library/similar",
    ].some((path) => pathname.startsWith(path)) ||
    (pathname === "/" && params.get("favorites") === "true");
  async function handleLogout() {
    await logout();
    setOpenedOnPath(null);
    router.push("/login");
  }
  return (
    <nav
      aria-label={t("Navigation")}
      className="shell-bottom-nav fixed bottom-0 left-0 z-40 flex min-h-16 w-full items-stretch border-t border-border bg-card px-1 pb-safe md:hidden"
    >
      {tabs.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/" && params.get("favorites") !== "true"
            : pathname.startsWith(item.href);
        return (
          <a
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`relative mx-0.5 my-1.5 flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-2xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted"}`}
            onClick={(event) => {
              if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey
              )
                return;
              event.preventDefault();
              router.push(item.href);
            }}
          >
            <item.icon className="h-5 w-5" strokeWidth={1.6} aria-hidden />
            {item.href === "/inbox" && (inbox.data ?? 0) > 0 && (
              <span className="absolute left-[calc(50%+4px)] top-0.5 min-w-4 rounded-full bg-secondary px-1 font-mono text-3xs leading-4 text-secondary-foreground">
                {(inbox.data ?? 0) > 99 ? "99+" : inbox.data}
              </span>
            )}
            <span>{t(item.labelKey)}</span>
          </a>
        );
      })}
      <DropdownMenu
        open={moreOpen}
        onOpenChange={(value) => setOpenedOnPath(value ? pathname : null)}
        portal
        role="menu"
        align="end"
        className="mx-0.5 my-1.5 flex min-w-0 flex-1"
        contentClassName="mobile-workspace-popover"
        trigger={
          <button
            type="button"
            data-menu-trigger
            aria-label={t("nav.more")}
            aria-expanded={moreOpen}
            aria-haspopup="menu"
            aria-current={moreActive ? "page" : undefined}
            className={`flex w-full flex-col items-center justify-center gap-0.5 text-2xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${moreActive || moreOpen ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted"}`}
            onClick={() => setOpenedOnPath(moreOpen ? null : pathname)}
          >
            <MoreHorizontal className="h-5 w-5" strokeWidth={1.6} aria-hidden />
            <span>{t("nav.more")}</span>
          </button>
        }
      >
        <WorkspaceMenu
          mobile
          pathname={pathname}
          username={user?.username ?? t("nav.account")}
          printersInQuickAccess={!!user?.is_superuser}
          isAdmin={!!user?.is_superuser}
          onNavigate={() => setOpenedOnPath(null)}
          onLogout={handleLogout}
        />
      </DropdownMenu>
    </nav>
  );
}
