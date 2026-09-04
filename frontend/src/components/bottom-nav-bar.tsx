"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/link";
import { usePathname, useRouter } from "@/lib/navigation";
import {
  BarChart3,
  Bell,
  BookOpen,
  Box,
  SlidersHorizontal,
  LogOut,
  MoreHorizontal,
  Inbox,
  Printer,
  Settings,
  User,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { Drawer } from "@/components/ui/drawer";
import { TaskList } from "@/components/task-list";
import { clearCompletedTasks, listTasks, subscribeTasks, type TaskItem } from "@/lib/task-center";
import { listPendingImports } from "@/lib/api";

type NavItem = {
  href: string;
  labelKey: MessageKey;
  icon: LucideIcon;
  adminOnly?: boolean;
  external?: boolean;
};

// Ordered by priority — the first few become the persistent tabs, the rest fold
// into the "More" sheet. Wiki and Settings sit last because they're also one tap
// away from the avatar menu in the top bar.
const NAV_ITEMS: NavItem[] = [
  { href: "/", labelKey: "nav.vault", icon: Box },
  { href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { href: "/printers", labelKey: "nav.printers", icon: Printer, adminOnly: true },
  { href: "/profiles", labelKey: "nav.profiles", icon: SlidersHorizontal },
  { href: "/statistics", labelKey: "nav.stats", icon: BarChart3, adminOnly: true },
  { href: "/settings", labelKey: "nav.settings", icon: Settings },
  {
    href: "https://xiao-villamor.github.io/PrintStash/",
    labelKey: "nav.wiki",
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
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  // The sheet remembers which route it was opened on, so navigating away closes
  // it by derivation instead of by an after-the-fact effect.
  const [openedOnPath, setOpenedOnPath] = useState<string | null>(null);
  const moreOpen = openedOnPath === pathname;
  const [tasks, setTasks] = useState<TaskItem[]>(listTasks);
  const [pendingImports, setPendingImports] = useState(0);

  const visibleItems = NAV_ITEMS.filter((item) => !item.adminOnly || user?.is_superuser);

  useEffect(() => subscribeTasks(() => setTasks(listTasks())), []);

  useEffect(() => {
    let active = true;
    const refresh = () =>
      listPendingImports(false)
        .then((rows) => {
          if (active) setPendingImports(rows.filter((row) => row.state !== "dismissed").length);
        })
        .catch(() => {});
    void refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [pathname]);

  const tabs = visibleItems.slice(0, MAX_TABS);
  const overflow = visibleItems.slice(MAX_TABS);
  const moreActive = overflow.some((item) => isItemActive(item, pathname));

  async function handleLogout() {
    await logout();
    setOpenedOnPath(null);
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
            badge={item.href === "/inbox" ? pendingImports : 0}
          />
        ))}
        <button
          type="button"
          onClick={() => setOpenedOnPath(pathname)}
          aria-label={t("nav.more")}
          aria-expanded={moreOpen}
          aria-current={moreActive ? "page" : undefined}
          className="group flex flex-1 flex-col items-center justify-center gap-1 pt-2 pb-1.5"
        >
          <TabIcon icon={MoreHorizontal} active={moreActive || moreOpen} />
          <TabLabel active={moreActive || moreOpen}>{t("nav.more")}</TabLabel>
        </button>
      </nav>

      <MoreSheet
        open={moreOpen}
        items={overflow}
        pathname={pathname}
        username={user?.username}
        tasks={tasks}
        onClearTasks={() => {
          clearCompletedTasks();
          setTasks(listTasks());
        }}
        onLogout={user ? handleLogout : undefined}
        onClose={() => setOpenedOnPath(null)}
      />
    </>
  );
}

function NavTab({ item, active, badge = 0 }: { item: NavItem; active: boolean; badge?: number }) {
  const { t } = useI18n();
  const className =
    "group flex flex-1 flex-col items-center justify-center gap-1 pt-2 pb-1.5 active:scale-95 transition-transform duration-press";
  const content = (
    <>
      <span className="relative">
        <TabIcon icon={item.icon} active={active} />
        {badge > 0 && (
          <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-destructive px-1 text-center text-3xs font-bold text-destructive-foreground">
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </span>
      <TabLabel active={active}>{t(item.labelKey)}</TabLabel>
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

function TabLabel({ active, children }: { active: boolean; children: React.ReactNode }) {
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
  tasks,
  onClearTasks,
  onLogout,
  onClose,
}: {
  open: boolean;
  items: NavItem[];
  pathname: string;
  username?: string;
  tasks: TaskItem[];
  onClearTasks: () => void;
  onLogout?: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [tasksOpen, setTasksOpen] = useState(false);
  const activeTasks = tasks.filter(
    (task) => task.status === "pending" || task.status === "running",
  ).length;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="bottom"
      ariaLabel={t("nav.more")}
      containerClassName="md:hidden"
      className="max-h-[85dvh] overflow-y-auto rounded-t-2xl border-t border-border bg-card px-4 pt-3 pb-safe shadow-2xl"
    >
      <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-muted-foreground/25" />
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">{t("nav.more")}</span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("nav.close")}
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
                <span className="text-xs font-medium">{t(item.labelKey)}</span>
              </>
            );
            if (item.external) {
              return (
                <a
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  onClick={onClose}
                  className={className}
                >
                  {inner}
                </a>
              );
            }
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                onClick={onClose}
                className={className}
              >
                {inner}
              </Link>
            );
          })}
        </div>
      )}

      <div className="mt-3 overflow-hidden rounded-lg border border-border bg-background">
        <button
          type="button"
          onClick={() => setTasksOpen((open) => !open)}
          aria-expanded={tasksOpen}
          className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <Bell className="h-4 w-4 text-muted-foreground" />
          <span className="flex-1">{t("nav.tasks")}</span>
          <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-3xs text-muted-foreground">
            {activeTasks ? `${activeTasks} active` : tasks.length}
          </span>
        </button>
        {tasksOpen && <TaskList tasks={tasks} onClear={onClearTasks} compact />}
      </div>

      {onLogout && (
        <div className="mt-3 flex items-center gap-3 border-t border-border pt-3 pb-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <User className="h-4 w-4" />
          </div>
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
            {username ?? t("nav.account")}
          </span>
          <button
            type="button"
            onClick={onLogout}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10"
          >
            <LogOut className="h-4 w-4" />
            {t("nav.logOut")}
          </button>
        </div>
      )}
    </Drawer>
  );
}
