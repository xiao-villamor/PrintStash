"use client";

import { Suspense, useEffect, useRef, useState, useTransition } from "react";
import { useRouter, useSearchParams, usePathname } from "@/lib/navigation";
import { Link } from "@/lib/navigation";
import {
  BarChart3,
  Bell,
  Box,
  CheckCircle2,
  ChevronDown,
  BookOpen,
  SlidersHorizontal,
  LogOut,
  Loader2,
  Printer,
  Search,
  Settings,
  XCircle,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { lastVaultHref } from "@/lib/last-collection";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import {
  clearCompletedTasks,
  listTasks,
  subscribeTasks,
  TaskItem,
} from "@/lib/task-center";

const WIKI_URL = "https://xiao-villamor.github.io/PrintStash/";

function TopBarSearch() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const [value, setValue] = useState(q);
  const inputRef = useRef<HTMLInputElement>(null);
  const [, startTransition] = useTransition();

  useEffect(() => { setValue(q); }, [q]);

  useEffect(() => {
    if (pathname !== "/") return;
    function focusSearch(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        event.key !== "/" ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        target?.matches("input, textarea, select, [contenteditable='true']")
      ) {
        return;
      }
      event.preventDefault();
      inputRef.current?.focus();
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, [pathname]);

  useEffect(() => {
    if (pathname !== "/") return;
    const handle = window.setTimeout(() => {
      const next = value.trim();
      if (next === q) return;
      const params = new URLSearchParams(searchParams.toString());
      if (next) params.set("q", next);
      else params.delete("q");
      const queryString = params.toString();
      startTransition(() => {
        router.replace(queryString ? `/?${queryString}` : "/", { scroll: false });
      });
    }, 250);
    return () => window.clearTimeout(handle);
  }, [pathname, q, router, searchParams, startTransition, value]);

  function clearSearch() {
    setValue("");
    const params = new URLSearchParams(searchParams.toString());
    params.delete("q");
    const queryString = params.toString();
    if (queryString) router.replace(`/?${queryString}`, { scroll: false });
    else router.replace("/", { scroll: false });
  }

  if (pathname !== "/") return <span className="flex-1" />;

  return (
    <div className="flex-1 max-w-2xl mx-3 sm:mx-8 block">
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <Search className="h-5 w-5 text-muted-foreground" />
        </div>
        <input
          ref={inputRef}
          className="block w-full pl-10 pr-10 sm:pr-14 py-2 border border-border rounded-lg leading-5 bg-muted text-foreground placeholder:text-muted-foreground focus:outline-none focus:bg-background focus:ring-1 focus:ring-ring focus:border-primary dark:border-primary-soft text-sm transition-colors"
          placeholder="Search PrintStash..."
          aria-label="Search models"
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <div className="absolute inset-y-0 right-0 pr-3 hidden sm:flex items-center pointer-events-none">
          <span className="text-xs text-muted-foreground border border-border rounded px-1.5 py-0.5">/</span>
        </div>
        {value && (
          <button
            type="button"
            onClick={clearSearch}
            className="absolute right-2 sm:right-10 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-muted-foreground pointer-events-auto"
            aria-label="Clear search"
          >
            <XCircle className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

export function TopBar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  // The logo returns to the model browser, restoring the last folder the user
  // was in rather than always resetting to "All Models". Recomputed whenever the
  // route changes (e.g. arriving on Settings) so it reflects the remembered
  // collection at click time.
  const [homeHref, setHomeHref] = useState("/");
  useEffect(() => {
    setHomeHref(lastVaultHref());
  }, [pathname]);

  useEffect(() => {
    setTasks(listTasks());
    return subscribeTasks(() => setTasks(listTasks()));
  }, []);

  function handleLogout() {
    logout();
    setProfileOpen(false);
    router.push("/login");
  }

  return (
    <header className="h-16 bg-background border-b border-border flex items-center justify-between px-4 z-40 relative">
      {/* Logo */}
      <Link href={homeHref} className="flex items-center space-x-2 hover:opacity-80 transition-opacity">
        <div className="w-8 h-8 bg-primary rounded flex items-center justify-center flex-shrink-0">
          <BrandMark className="h-6 w-6 text-primary-foreground" />
        </div>
        <span className="text-xl font-bold text-foreground tracking-tight hidden sm:block">PrintStash</span>
      </Link>

      {/* Search */}
      <Suspense fallback={<span className="flex-1" />}>
        <TopBarSearch />
      </Suspense>

      {/* Right Actions & Profile */}
      <div className="flex items-center space-x-4">
        <a
          href={WIKI_URL}
          className="hidden sm:flex items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <BookOpen className="h-4 w-4" />
          Wiki
        </a>
        <ThemeToggle />
        <DropdownMenu
          open={tasksOpen}
          onOpenChange={setTasksOpen}
          align="end"
          role="dialog"
          className="hidden sm:flex"
          trigger={
            <button
              type="button"
              data-menu-trigger
              onClick={() => setTasksOpen((v) => !v)}
              className="relative text-muted-foreground hover:text-foreground p-1 rounded-full hover:bg-muted transition-colors"
              aria-label="Notifications"
              title="Notifications"
              aria-haspopup="dialog"
              aria-expanded={tasksOpen}
            >
              <Bell className="h-4 w-4" />
              {tasks.some((t) => t.status === "pending" || t.status === "running") && (
                <span className="absolute top-0.5 right-0.5 h-2 w-2 rounded-full bg-primary ring-2 ring-background" />
              )}
            </button>
          }
        >
          <TaskPopover
            tasks={tasks}
            onClear={() => {
              clearCompletedTasks();
              setTasks(listTasks());
            }}
          />
        </DropdownMenu>
        <div className="h-8 w-px bg-muted hidden sm:block" />
        {/* Profile — hidden on mobile, where the bottom nav's "More" sheet owns
            the account actions (the avatar menu only duplicated nav links). */}
        {!loading && !user ? (
          <div className="relative hidden sm:block">
            <Link
              href="/login"
              className="flex items-center px-3 py-1.5 text-sm font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover transition-colors"
            >
              Log In
            </Link>
          </div>
        ) : (
          <DropdownMenu
            open={profileOpen}
            onOpenChange={setProfileOpen}
            align="end"
            role="menu"
            className="hidden sm:block"
            trigger={
              <button
                type="button"
                data-menu-trigger
                onClick={() => setProfileOpen((v) => !v)}
                className="group flex items-center space-x-2 rounded-md px-1.5 py-1 transition-[background-color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 hover:bg-muted"
                aria-haspopup="menu"
                aria-expanded={profileOpen}
              >
                <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-muted-foreground font-medium text-sm">
                  {user?.username.charAt(0).toUpperCase() ?? "…"}
                </div>
                <span className="text-sm font-medium text-foreground group-hover:text-foreground hidden sm:block">
                  {user?.username ?? "…"}
                </span>
                <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform group-hover:text-muted-foreground hidden sm:block ${profileOpen ? "rotate-180" : ""}`} />
              </button>
            }
          >
            <ProfileMenu
              pathname={pathname}
              isAdmin={!!user?.is_superuser}
              onNavigate={() => setProfileOpen(false)}
              onLogout={handleLogout}
            />
          </DropdownMenu>
        )}
      </div>
    </header>
  );
}

function ProfileMenu({
  pathname,
  isAdmin,
  onNavigate,
  onLogout,
}: {
  pathname: string;
  isAdmin: boolean;
  onNavigate: () => void;
  onLogout: () => void;
}) {
  const items = [
    { href: "/", label: "Vault", icon: Box },
    { href: "/printers", label: "Printers", icon: Printer, adminOnly: true },
    { href: "/statistics", label: "Statistics", icon: BarChart3, adminOnly: true },
    { href: "/profiles", label: "Profiles", icon: SlidersHorizontal },
    { href: WIKI_URL, label: "Wiki", icon: BookOpen, external: true },
    { href: "/settings", label: "Settings", icon: Settings },
  ].filter((item) => !item.adminOnly || isAdmin);

  return (
    <div
      className="w-48 overflow-hidden rounded-lg border border-border bg-popover py-1 shadow-xl"
    >
      {items.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        const className = `flex items-center gap-3 px-3 py-2.5 text-sm transition-colors ${
          active
            ? "bg-accent text-accent-foreground"
            : "text-foreground hover:bg-popover-hover hover:text-foreground"
        } focus-visible:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset ${item.href === WIKI_URL ? "border-t border-border" : ""}`;
        if (item.external) {
          return (
            <a key={item.href} href={item.href} role="menuitem" onClick={onNavigate} className={className}>
              <item.icon className="h-4 w-4" />
              <span>{item.label}</span>
            </a>
          );
        }
        return (
          <Link key={item.href} href={item.href} role="menuitem" aria-current={active ? "page" : undefined} onClick={onNavigate} className={className}>
            <item.icon className="h-4 w-4" />
            <span>{item.label}</span>
          </Link>
        );
      })}
      <button
        type="button"
        role="menuitem"
        onClick={onLogout}
        className="flex w-full items-center gap-3 rounded-md border-t border-border px-3 py-2.5 text-left text-sm text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
      >
        <LogOut className="h-4 w-4" />
        <span>Log Out</span>
      </button>
    </div>
  );
}

function TaskPopover({
  tasks,
  onClear,
}: {
  tasks: TaskItem[];
  onClear: () => void;
}) {
  return (
    <div className="w-[360px] max-w-[calc(100vw-2rem)] rounded border border-border bg-popover shadow-lg">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">Tasks</span>
        {tasks.some((t) => t.status === "completed" || t.status === "failed") && (
          <button onClick={onClear} className="font-mono text-3xs uppercase tracking-wider text-muted-foreground hover:text-foreground">
            Clear done
          </button>
        )}
      </div>
      {tasks.length === 0 ? (
        <div className="px-4 py-8 text-center font-mono text-xs text-muted-foreground">No active tasks</div>
      ) : (
        <div className="max-h-[420px] overflow-y-auto py-2">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

function TaskRow({ task }: { task: TaskItem }) {
  const active = task.status === "pending" || task.status === "running";
  return (
    <div className="px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5">
          {active ? (
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
          ) : task.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          ) : (
            <XCircle className="h-4 w-4 text-red-500" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-medium text-foreground">{task.title}</p>
            <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">{task.status}</span>
          </div>
          {task.detail && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{task.detail}</p>
          )}
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-muted">
            <div
              className={`h-full w-full origin-left transition-transform duration-slow ease-linear ${task.status === "failed" ? "bg-red-500" : "bg-primary"}`}
              style={{ transform: `scaleX(${Math.min(100, task.progress) / 100})` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
