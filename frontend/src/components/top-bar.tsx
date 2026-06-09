"use client";

import { Suspense, useEffect, useRef, useState, useTransition } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import Link from "next/link";
import {
  Bell,
  Box,
  CheckCircle2,
  ChevronDown,
  DollarSign,
  FolderTree,
  LogOut,
  Loader2,
  Printer,
  Search,
  Settings,
  XCircle,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  clearCompletedTasks,
  listTasks,
  subscribeTasks,
  TaskItem,
} from "@/lib/task-center";

function TopBarSearch() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const [value, setValue] = useState(q);
  const [, startTransition] = useTransition();

  useEffect(() => { setValue(q); }, [q]);

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
    <div className="flex-1 max-w-2xl mx-8 hidden sm:block">
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <Search className="h-5 w-5 text-muted-foreground" />
        </div>
        <input
          className="block w-full pl-10 pr-14 py-2 border border-border rounded-lg leading-5 bg-muted/50 placeholder-slate-400 focus:outline-none focus:bg-background focus:ring-1 focus:ring-blue-500 dark:focus:ring-orange-500 focus:border-blue-500 dark:focus:border-blue-500 dark:border-orange-500 sm:text-sm transition-colors"
          placeholder="Search PrintStash..."
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <div className="absolute inset-y-0 right-0 pr-3 flex items-center pointer-events-none">
          <span className="text-xs text-muted-foreground border border-border rounded px-1.5 py-0.5">/</span>
        </div>
        {value && (
          <button
            type="button"
            onClick={clearSearch}
            className="absolute right-10 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-muted-foreground pointer-events-auto"
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
  const tasksRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTasks(listTasks());
    return subscribeTasks(() => setTasks(listTasks()));
  }, []);

  useEffect(() => {
    if (!tasksOpen && !profileOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!tasksRef.current?.contains(event.target as Node)) setTasksOpen(false);
      if (!profileRef.current?.contains(event.target as Node)) setProfileOpen(false);
    }
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [tasksOpen, profileOpen]);

  function handleLogout() {
    logout();
    setProfileOpen(false);
    router.push("/login");
  }

  return (
    <header className="h-16 bg-background border-b border-border flex items-center justify-between px-4 z-40 relative">
      {/* Logo */}
      <Link href="/" className="flex items-center space-x-2 hover:opacity-80 transition-opacity">
        <div className="w-8 h-8 bg-blue-600 dark:bg-orange-600 rounded flex items-center justify-center flex-shrink-0">
          <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
          </svg>
        </div>
        <span className="text-xl font-bold text-foreground tracking-tight hidden sm:block">PrintStash</span>
      </Link>

      {/* Search */}
      <Suspense fallback={<span className="flex-1" />}>
        <TopBarSearch />
      </Suspense>

      {/* Right Actions & Profile */}
      <div className="flex items-center space-x-4">
        <ThemeToggle />
        <div ref={tasksRef} className="relative hidden sm:flex">
          <button
            type="button"
            onClick={() => setTasksOpen((v) => !v)}
            className="relative text-muted-foreground hover:text-foreground p-1 rounded-full hover:bg-muted transition-colors"
            aria-label="Notifications"
            title="Notifications"
          >
            <Bell className="h-4 w-4" />
            {tasks.some((t) => t.status === "pending" || t.status === "running") && (
              <span className="absolute top-0.5 right-0.5 h-2 w-2 rounded-full bg-blue-500 dark:bg-orange-500 ring-2 ring-background" />
            )}
          </button>
          {tasksOpen && (
            <TaskPopover
              tasks={tasks}
              onClear={() => {
                clearCompletedTasks();
                setTasks(listTasks());
              }}
            />
          )}
        </div>
        <div className="h-8 w-px bg-muted hidden sm:block" />
        {/* Profile */}
        <div ref={profileRef} className="relative">
          {!loading && !user ? (
            <Link
              href="/login"
              className="flex items-center px-3 py-1.5 text-sm font-medium text-white bg-blue-600 dark:bg-orange-600 rounded hover:bg-blue-700 dark:hover:bg-orange-700 transition-colors"
            >
              Log In
            </Link>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setProfileOpen((v) => !v)}
                className="flex items-center space-x-2 focus:outline-none group"
                aria-haspopup="menu"
                aria-expanded={profileOpen}
              >
                <div className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center text-white font-medium text-sm">
                  {user?.username.charAt(0).toUpperCase() ?? "…"}
                </div>
                <span className="text-sm font-medium text-foreground group-hover:text-foreground hidden sm:block">
                  {user?.username ?? "…"}
                </span>
                <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform group-hover:text-muted-foreground hidden sm:block ${profileOpen ? "rotate-180" : ""}`} />
              </button>
              {profileOpen && (
                <ProfileMenu
                  pathname={pathname}
                  onNavigate={() => setProfileOpen(false)}
                  onLogout={handleLogout}
                />
              )}
            </>
          )}
        </div>
      </div>
    </header>
  );
}

function ProfileMenu({
  pathname,
  onNavigate,
  onLogout,
}: {
  pathname: string;
  onNavigate: () => void;
  onLogout: () => void;
}) {
  const items = [
    { href: "/", label: "Vault", icon: Box },
    { href: "/printers", label: "Printers", icon: Printer },
    { href: "/profiles", label: "Profiles", icon: DollarSign },
    { href: "/organize", label: "Catalog", icon: FolderTree },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  return (
    <div
      role="menu"
      className="absolute right-0 top-full mt-3 w-48 overflow-hidden rounded border border-border bg-popover py-1 shadow-lg"
    >
      {items.map((item, index) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            role="menuitem"
            onClick={onNavigate}
            className={`flex items-center gap-3 px-3 py-2.5 text-sm transition-colors ${
              active
                ? "bg-blue-50 dark:bg-orange-500/12 text-blue-700 dark:text-orange-400"
                : "text-foreground hover:bg-muted/50 hover:text-foreground"
            } ${index === 4 ? "border-t border-border" : ""}`}
          >
            <item.icon className="h-4 w-4" />
            <span>{item.label}</span>
          </Link>
        );
      })}
      <button
        type="button"
        role="menuitem"
        onClick={onLogout}
        className="flex w-full items-center gap-3 border-t border-border px-3 py-2.5 text-left text-sm text-red-600 transition-colors hover:bg-red-500/10"
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
    <div className="absolute right-0 top-full mt-2 w-[360px] max-w-[calc(100vw-2rem)] rounded border border-border bg-popover shadow-lg">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Tasks</span>
        {tasks.some((t) => t.status === "completed" || t.status === "failed") && (
          <button onClick={onClear} className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground hover:text-foreground">
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
            <Loader2 className="h-4 w-4 animate-spin text-blue-600 dark:text-orange-500" />
          ) : task.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          ) : (
            <XCircle className="h-4 w-4 text-red-500" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-medium text-foreground">{task.title}</p>
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{task.status}</span>
          </div>
          {task.detail && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{task.detail}</p>
          )}
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-muted">
            <div
              className={`h-full transition-all duration-300 ${task.status === "failed" ? "bg-red-500" : "bg-blue-600 dark:bg-orange-600"}`}
              style={{ width: `${task.progress}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
