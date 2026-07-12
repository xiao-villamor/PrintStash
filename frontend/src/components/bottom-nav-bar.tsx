"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/navigation";
import { usePathname, useRouter } from "@/lib/navigation";
import {
  BarChart3,
  BookOpen,
  Box,
  SlidersHorizontal,
  FolderTree,
  LogOut,
  MoreHorizontal,
  Printer,
  Settings,
  User,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { Drawer } from "@/components/ui/drawer";

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  external?: boolean;
};

// Ordered by priority — the first few become the persistent tabs, the rest fold
// into the "More" sheet. Wiki and Settings sit last because they're also one tap
// away from the avatar menu in the top bar.
const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Vault", icon: Box },
  { href: "/printers", label: "Printers", icon: Printer, adminOnly: true },
  { href: "/statistics", label: "Stats", icon: BarChart3, adminOnly: true },
  { href: "/profiles", label: "Profiles", icon: SlidersHorizontal },
  { href: "/organize", label: "Catalog", icon: FolderTree },
  { href: "/settings", label: "Settings", icon: Settings },
  {
    href: "https://xiao-villamor.github.io/PrintStash/",
    label: "Wiki",
    icon: BookOpen,
    external: true,
  },
];

// Persistent tabs shown in the bar. The final slot is always a "More" button
// (5 slots total): it holds any overflow destinations plus the account actions,
// so the tabs never get crushed and logging out is always one tap away.
const MAX_TABS = 4;

function isItemActive(item: NavItem, pathname: string): boolean {
  if (item.external) return false;
  return item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
}

export function BottomNavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);

  const visibleItems = NAV_ITEMS.filter(
    (item) => !item.adminOnly || user?.is_superuser,
  );

  // Close the sheet on navigation.
  useEffect(() => {
    setMoreOpen(false);
  }, [pathname]);

  const tabs = visibleItems.slice(0, MAX_TABS);
  const overflow = visibleItems.slice(MAX_TABS);
  const moreActive = overflow.some((item) => isItemActive(item, pathname));

  function handleLogout() {
    logout();
    setMoreOpen(false);
    router.push("/login");
  }

  return (
    <>
      <nav className="md:hidden fixed bottom-0 left-0 z-40 flex w-full items-stretch border-t border-border bg-card/95 backdrop-blur-md pb-safe">
        {tabs.map((item) => (
          <NavTab
            key={item.href}
            item={item}
            active={isItemActive(item, pathname)}
          />
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-label="More"
          aria-expanded={moreOpen}
          aria-current={moreActive ? "page" : undefined}
          className="group flex flex-1 flex-col items-center justify-center gap-1 pt-2 pb-1.5"
        >
          <TabIcon icon={MoreHorizontal} active={moreActive || moreOpen} />
          <TabLabel active={moreActive || moreOpen}>More</TabLabel>
        </button>
      </nav>

      <MoreSheet
        open={moreOpen}
        items={overflow}
        pathname={pathname}
        username={user?.username}
        onLogout={user ? handleLogout : undefined}
        onClose={() => setMoreOpen(false)}
      />
    </>
  );
}

function NavTab({ item, active }: { item: NavItem; active: boolean }) {
  const className =
    "group flex flex-1 flex-col items-center justify-center gap-1 pt-2 pb-1.5 active:scale-95 transition-transform duration-press";
  const content = (
    <>
      <TabIcon icon={item.icon} active={active} />
      <TabLabel active={active}>{item.label}</TabLabel>
    </>
  );
  if (item.external) {
    return (
      <a href={item.href} className={className} aria-current={active ? "page" : undefined}>
        {content}
      </a>
    );
  }
  return (
    <Link href={item.href} className={className} aria-current={active ? "page" : undefined}>
      {content}
    </Link>
  );
}

// The active destination gets a filled "pill" behind the icon (Material-style),
// so the highlight reads clearly without the label crowding into its neighbours.
function TabIcon({ icon: Icon, active }: { icon: LucideIcon; active: boolean }) {
  return (
    <span
      className={`flex h-7 w-[3.25rem] items-center justify-center rounded-full transition-colors ${
        active
          ? "bg-accent text-primary"
          : "text-muted-foreground group-hover:bg-muted group-hover:text-foreground"
      }`}
    >
      <Icon className="h-5 w-5" {...(active ? { strokeWidth: 2.4 } : {})} />
    </span>
  );
}

function TabLabel({
  active,
  children,
}: {
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`text-3xs font-medium leading-none tracking-tight ${
        active ? "text-primary" : "text-muted-foreground"
      }`}
    >
      {children}
    </span>
  );
}

function MoreSheet({
  open,
  items,
  pathname,
  username,
  onLogout,
  onClose,
}: {
  open: boolean;
  items: NavItem[];
  pathname: string;
  username?: string;
  onLogout?: () => void;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="bottom"
      ariaLabel="More"
      containerClassName="md:hidden"
      className="rounded-t-2xl border-t border-border bg-card px-4 pt-3 pb-safe shadow-2xl"
    >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-muted-foreground/25" />
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-semibold text-foreground">More</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {items.length > 0 && (
          <div className="grid grid-cols-3 gap-2">
            {items.map((item) => {
              const active = isItemActive(item, pathname);
              const className = `flex flex-col items-center justify-center gap-2 rounded-xl border p-4 text-center transition-[color,background-color,border-color,transform] duration-press active:scale-[0.98] ${
                active
                  ? "border-primary-soft bg-accent text-primary"
                  : "border-border bg-background text-foreground hover:bg-muted"
              }`;
              const inner = (
                <>
                  <item.icon className="h-5 w-5" />
                  <span className="text-xs font-medium">{item.label}</span>
                </>
              );
              if (item.external) {
                return (
                  <a key={item.href} href={item.href} aria-current={active ? "page" : undefined} onClick={onClose} className={className}>
                    {inner}
                  </a>
                );
              }
              return (
                <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} onClick={onClose} className={className}>
                  {inner}
                </Link>
              );
            })}
          </div>
        )}

        {onLogout && (
          <div className="mt-3 flex items-center gap-3 border-t border-border pt-3 pb-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <User className="h-4 w-4" />
            </div>
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              {username ?? "Account"}
            </span>
            <button
              type="button"
              onClick={onLogout}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10"
            >
              <LogOut className="h-4 w-4" />
              Log out
            </button>
          </div>
        )}
    </Drawer>
  );
}
