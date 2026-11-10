"use client";

import {
  BarChart3,
  BookOpen,
  Box,
  Inbox,
  Layers,
  LogOut,
  Printer,
  Settings,
  SlidersHorizontal,
} from "lucide-react";

import { LocaleToggle } from "@/components/locale-toggle";
import { ThemeToggle } from "@/components/theme-toggle";

import { useI18n, useUiLocale } from "@/lib/i18n";
import { Link } from "@/lib/link";

export function WorkspaceMenu({
  pathname,
  username,
  printersInQuickAccess,
  isAdmin,
  onNavigate,
  onLogout,
}: {
  pathname: string;
  username: string;
  printersInQuickAccess: boolean;
  isAdmin: boolean;
  onNavigate: () => void;
  onLogout: () => void;
}) {
  useUiLocale();
  const { t } = useI18n();
  const items = [
    { href: "/", label: t("nav.vault"), icon: Box },
    { href: "/inbox", label: t("nav.inbox"), icon: Inbox },
    { href: "/library/similar", label: t("similarity.title"), icon: Layers },
    { href: "/printers", label: t("nav.printers"), icon: Printer, adminOnly: true },
    { href: "/statistics", label: t("nav.statistics"), icon: BarChart3, adminOnly: true },
    { href: "/profiles", label: t("nav.profiles"), icon: SlidersHorizontal },
    { href: "/settings", label: t("nav.settings"), icon: Settings },
  ].filter(
    (item) => (!item.adminOnly || isAdmin) && !(item.href === "/printers" && printersInQuickAccess),
  );

  return (
    <div className="w-64 overflow-hidden rounded-lg border border-border bg-popover py-1 shadow-xl">
      <div className="border-b border-border px-3 py-2">
        <p className="truncate text-sm font-medium text-popover-foreground" title={username}>
          {username}
        </p>
        <p className="text-xs text-muted-foreground">{t("nav.myWorkspace")}</p>
      </div>
      {items.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        const className = `flex items-center gap-3 px-3 py-2.5 text-sm transition-colors ${
          active
            ? "bg-accent text-accent-foreground"
            : "text-foreground hover:bg-popover-hover hover:text-foreground"
        } focus-visible:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset`;
        return (
          <Link
            key={item.href}
            href={item.href}
            role="menuitem"
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
            className={className}
          >
            <item.icon className="h-4 w-4" />
            <span>{item.label}</span>
          </Link>
        );
      })}
      <div className="border-t border-border py-1">
        <ThemeToggle menuItem />
        <LocaleToggle menuItem />
        <a
          href="https://www.printstash.org/docs/"
          role="menuitem"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 px-3 py-2.5 text-sm text-foreground hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <BookOpen className="h-4 w-4" />
          <span>{t("nav.wiki")}</span>
        </a>
      </div>
      <button
        type="button"
        role="menuitem"
        onClick={onLogout}
        className="flex w-full items-center gap-3 rounded-md border-t border-border px-3 py-2.5 text-left text-sm text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
      >
        <LogOut className="h-4 w-4" />
        <span>{t("nav.logOut")}</span>
      </button>
    </div>
  );
}
