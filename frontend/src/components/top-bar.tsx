"use client";

import { Suspense, useEffect, useRef, useState, useTransition } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Bell, CheckCircle2, HelpCircle, Loader2, Search, XCircle } from "lucide-react";
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

  useEffect(() => {
    setValue(q);
  }, [q]);

  useEffect(() => {
    if (pathname !== "/") return;
    const handle = window.setTimeout(() => {
      const next = value.trim();
      if (next === q) return;
      const params = new URLSearchParams(searchParams.toString());
      if (next) {
        params.set("q", next);
      } else {
        params.delete("q");
      }
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
    if (queryString) {
      router.replace(`/?${queryString}`, { scroll: false });
    } else {
      router.replace("/", { scroll: false });
    }
  }

  if (pathname !== "/") return <span className="flex-1" />;

  return (
    <div className="flex-1 flex justify-center">
      <div className="relative w-full max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--on-surface-variant)]" />
        <input
          className="w-full h-9 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded pl-10 pr-10 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent transition-shadow placeholder:text-[var(--on-surface-variant)]/50"
          placeholder="Search models..."
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        {value && (
          <button
            type="button"
            onClick={clearSearch}
            className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container)] hover:text-[var(--on-surface)]"
            aria-label="Clear search"
            title="Clear search"
          >
            <XCircle className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

export function TopBar() {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);
  const activeCount = tasks.filter(
    (task) => task.status === "pending" || task.status === "running",
  ).length;

  useEffect(() => {
    setTasks(listTasks());
    return subscribeTasks(() => setTasks(listTasks()));
  }, []);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!popoverRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  return (
    <header className="bg-[var(--surface-container-lowest)] h-16 sticky top-0 z-40 border-b border-[var(--outline-variant)] flex items-center px-4 md:px-6 w-full gap-2 md:gap-4">
      <span className="md:hidden font-headline-sm text-[18px] font-bold text-[var(--primary)] tracking-tight">
        PrintStash
      </span>

      <Suspense fallback={<span className="flex-1" />}>
        <TopBarSearch />
      </Suspense>

      <div className="flex items-center gap-1 md:gap-2">
        <ThemeToggle />
        <div ref={popoverRef} className="relative hidden sm:flex">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            className="relative inline-flex h-9 w-9 items-center justify-center rounded text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container)] hover:text-[var(--primary)]"
            aria-label="Task notifications"
            title="Task notifications"
          >
            <Bell className="h-5 w-5" />
            {activeCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-[var(--primary)] ring-2 ring-[var(--surface-container-lowest)]" />
            )}
          </button>
          {open && (
            <TaskPopover
              tasks={tasks}
              onClear={() => {
                clearCompletedTasks();
                setTasks(listTasks());
              }}
            />
          )}
        </div>
        <button
          type="button"
          className="hidden h-9 w-9 items-center justify-center rounded text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container)] hover:text-[var(--primary)] sm:inline-flex"
          aria-label="Help"
          title="Help"
        >
          <HelpCircle className="h-5 w-5" />
        </button>
      </div>
    </header>
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
    <div className="absolute right-0 top-full mt-2 w-[360px] max-w-[calc(100vw-2rem)] rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] shadow-lg">
      <div className="flex items-center justify-between border-b border-[var(--outline-variant)] px-4 py-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-[var(--on-surface-variant)]">
          Tasks
        </span>
        {tasks.some((task) => task.status === "completed" || task.status === "failed") && (
          <button
            onClick={onClear}
            className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
          >
            Clear done
          </button>
        )}
      </div>
      {tasks.length === 0 ? (
        <div className="px-4 py-8 text-center font-mono text-xs text-[var(--on-surface-variant)]">
          No active tasks
        </div>
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
            <Loader2 className="h-4 w-4 animate-spin text-[var(--primary)]" />
          ) : task.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          ) : (
            <XCircle className="h-4 w-4 text-[var(--error)]" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-medium text-[var(--on-surface)]">
              {task.title}
            </p>
            <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
              {task.status}
            </span>
          </div>
          {task.detail && (
            <p className="mt-0.5 line-clamp-2 text-xs text-[var(--on-surface-variant)]">
              {task.detail}
            </p>
          )}
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-[var(--surface-container-high)]">
            <div
              className={`h-full transition-all duration-300 ${
                task.status === "failed" ? "bg-[var(--error)]" : "bg-[var(--primary)]"
              }`}
              style={{ width: `${task.progress}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
