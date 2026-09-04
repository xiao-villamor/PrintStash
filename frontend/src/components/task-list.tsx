"use client";

import { CheckCircle2, ChevronDown, Loader2, XCircle } from "lucide-react";

import type { TaskItem } from "@/lib/task-center";
import { Link } from "@/lib/link";

export function TaskList({
  tasks,
  onClear,
  compact = false,
}: {
  tasks: TaskItem[];
  onClear: () => void;
  compact?: boolean;
}) {
  return (
    <div
      className={
        compact
          ? "w-full"
          : "w-[360px] max-w-[calc(100vw-2rem)] rounded border border-border bg-popover shadow-lg"
      }
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">
          Tasks
        </span>
        {tasks.some((task) => task.status === "completed" || task.status === "failed") && (
          <button
            type="button"
            onClick={onClear}
            className="rounded font-mono text-3xs uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Clear done
          </button>
        )}
      </div>
      {tasks.length === 0 ? (
        <div className="px-4 py-8 text-center font-mono text-xs text-muted-foreground">
          No active tasks
        </div>
      ) : (
        <div
          className={
            compact ? "max-h-64 overflow-y-auto py-2" : "max-h-[420px] overflow-y-auto py-2"
          }
        >
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
            <CheckCircle2 className="h-4 w-4 text-success" />
          ) : (
            <XCircle className="h-4 w-4 text-destructive" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-medium text-foreground">{task.title}</p>
            <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
              {task.status}
            </span>
          </div>
          {task.detail && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{task.detail}</p>
          )}
          {active && task.total == null && (
            <p className="mt-1 text-xs text-muted-foreground">
              Discovering total… Safe to close this view.
            </p>
          )}
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-muted">
            <div
              className={`h-full w-full origin-left transition-transform duration-slow ease-linear ${task.status === "failed" ? "bg-destructive" : "bg-primary"}`}
              style={{ transform: `scaleX(${Math.min(100, task.progress) / 100})` }}
            />
          </div>
          {!!task.failedItems?.length && (
            <details className="mt-2 text-xs text-muted-foreground">
              <summary className="flex cursor-pointer list-none items-center gap-1 font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <ChevronDown className="h-3.5 w-3.5" /> Failed item details
              </summary>
              <ul className="mt-2 space-y-1">
                {task.failedItems.map((item, index) => (
                  <li key={`${item.name}-${index}`} className="break-words">
                    <span className="font-medium text-foreground">{item.name}</span>: {item.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {task.retryable && !active && (
            <button
              type="button"
              onClick={() => window.dispatchEvent(new CustomEvent("printstash:review-import"))}
              className="mt-2 rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Review and retry
            </button>
          )}
          {task.thumbnailStatus === "failed" && !active && (
            <Link
              href="/settings?section=maintenance"
              className="mt-2 inline-flex rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Repair thumbnail
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
