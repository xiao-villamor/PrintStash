"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/link";
import { usePathname } from "@/lib/navigation";
import {
  BookOpen,
  Box,
  Inbox,
  SlidersHorizontal,
  LogIn,
  LogOut,
  Printer,
  Settings,
  User,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { BrandMark } from "@/components/brand-mark";
import { listPendingImports } from "@/lib/api";
import { useI18n, type MessageKey } from "@/lib/i18n";

type NavItem = {
  href: string;
  labelKey: MessageKey;
  icon: LucideIcon;
  adminOnly?: boolean;
  external?: boolean;
};

const mainItems: NavItem[] = [
  { href: "/", labelKey: "nav.vault", icon: Box },
  { href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { href: "/printers", labelKey: "nav.printers", icon: Printer, adminOnly: true },
  { href: "/profiles", labelKey: "nav.profiles", icon: SlidersHorizontal },
  {
    href: "https://xiao-villamor.github.io/PrintStash/",
    labelKey: "nav.wiki",
    icon: BookOpen,
    external: true,
  },
];

const bottomItems: NavItem[] = [{ href: "/settings", labelKey: "nav.settings", icon: Settings }];

export function SidebarNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const { t } = useI18n();
  // The badge count is tagged with the account it was fetched for, so a signed-out
  // or freshly switched user never sees the previous account's inbox total while
  // the refetch is still in flight.
  const [pending, setPending] = useState<{ userId: number; count: number } | null>(null);
  useEffect(() => {
    if (!user) return;
    const userId = user.id;
    listPendingImports(false)
      .then((items) =>
        setPending({ userId, count: items.filter((item) => item.state !== "dismissed").length }),
      )
      .catch(() => setPending({ userId, count: 0 }));
  }, [pathname, user]);
  const pendingCount = user && pending?.userId === user.id ? pending.count : 0;
  const visibleMainItems = mainItems.filter((item) => !item.adminOnly || user?.is_superuser);

  return (
    <nav className="bg-card border-r border-border h-screen w-64 fixed left-0 top-0 flex-col py-6 px-4 z-50 hidden md:flex">
      <div className="flex items-center gap-4 mb-10 px-1">
        <div className="w-10 h-10 rounded bg-primary flex items-center justify-center text-primary-foreground flex-shrink-0">
          <BrandMark className="h-7 w-7" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-foreground leading-tight tracking-tight">
            PrintStash
          </h1>
          <p className="text-2xs text-muted-foreground font-mono">Your prints, organized</p>
        </div>
      </div>

      <div className="flex flex-col gap-1 flex-1">
        {visibleMainItems.map((item) => {
          const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const className = `flex items-center gap-4 px-3 py-2 rounded text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] ${
            isActive ? "text-accent-foreground bg-accent" : "text-muted-foreground hover:bg-muted"
          }`;
          if (item.external) {
            return (
              <a key={item.href} href={item.href} className={className}>
                <item.icon className="h-5 w-5" />
                <span className="font-mono text-xs tracking-wider uppercase">
                  {t(item.labelKey)}
                </span>
              </a>
            );
          }
          return (
            <Link key={item.href} href={item.href} className={className}>
              <item.icon className="h-5 w-5" />
              <span className="font-mono text-xs tracking-wider uppercase">{t(item.labelKey)}</span>
              {item.href === "/inbox" && pendingCount > 0 && (
                <span className="ml-auto rounded-full bg-accent px-2 py-0.5 font-mono text-3xs text-accent-foreground">
                  {pendingCount}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      {/* Bottom section */}
      <div className="flex flex-col gap-1">
        {user ? (
          <div className="flex items-center gap-3 px-3 py-2 mb-1">
            <div className="w-7 h-7 rounded-full bg-slate-800 flex items-center justify-center text-white">
              <User className="h-3.5 w-3.5" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-foreground truncate">{user.username}</p>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              className="text-muted-foreground hover:text-red-500 transition-colors p-1"
              title="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <Link
            href="/login"
            className={`flex items-center gap-4 px-3 py-2 rounded text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] mb-1 ${
              pathname.startsWith("/login")
                ? "text-accent-foreground bg-accent"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            <LogIn className="h-5 w-5" />
            <span className="font-mono text-xs tracking-wider uppercase">{t("nav.signIn")}</span>
          </Link>
        )}

        {bottomItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-4 px-3 py-2 rounded text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] ${
                isActive
                  ? "text-accent-foreground bg-accent"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              <item.icon className="h-5 w-5" />
              <span className="font-mono text-xs tracking-wider uppercase">{t(item.labelKey)}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
