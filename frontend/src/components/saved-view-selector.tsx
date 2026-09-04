"use client";

import { useMemo, useState } from "react";
import {
  Bookmark,
  BookmarkPlus,
  Check,
  Copy,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import type { SavedViewRead } from "@/types";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Localized } from "@/components/ui/localized";
import { cn } from "@/lib/utils";

const RECENT_KEY = "ps-recent-saved-views";

/**
 * Decode the persisted recent-view list. Anything that is not an integer id
 * (hand-edited storage, a payload from an older schema) is discarded rather
 * than trusted, so the rest of the component only ever sees view ids.
 */
function parseRecentIds(raw: string | null): number[] {
  if (raw === null) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id) => Number.isInteger(id));
  } catch {
    return [];
  }
}

function readRecent(): number[] {
  if (!("localStorage" in globalThis)) return [];
  return parseRecentIds(localStorage.getItem(RECENT_KEY));
}

export function SavedViewSelector({
  views,
  activeId,
  modified = false,
  onSelect,
  onCreate,
  onUpdate,
  onRename,
  onDuplicate,
  onDelete,
  triggerClassName,
  triggerRole,
  triggerSize = "xs",
  triggerVariant = "outline",
}: {
  views: SavedViewRead[];
  activeId: number | null;
  modified?: boolean;
  onSelect: (view: SavedViewRead) => void;
  onCreate: () => void;
  onUpdate: (view: SavedViewRead) => Promise<void>;
  onRename: (view: SavedViewRead, name: string) => Promise<void>;
  onDuplicate: (view: SavedViewRead) => Promise<void>;
  onDelete: (view: SavedViewRead) => Promise<void>;
  triggerClassName?: string;
  triggerRole?: "menuitem";
  triggerSize?: "xs" | "sm";
  triggerVariant?: "outline" | "ghost";
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [recentIds, setRecentIds] = useState<number[]>(readRecent);
  const [editing, setEditing] = useState<SavedViewRead | null>(null);
  const [deleting, setDeleting] = useState<SavedViewRead | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const matches = needle
      ? views.filter((view) => view.name.toLocaleLowerCase().includes(needle))
      : views;
    return [...matches].sort((a, b) => {
      const aRecent = recentIds.indexOf(a.id);
      const bRecent = recentIds.indexOf(b.id);
      if (aRecent >= 0 || bRecent >= 0)
        return (aRecent < 0 ? 99 : aRecent) - (bRecent < 0 ? 99 : bRecent);
      return a.name.localeCompare(b.name);
    });
  }, [query, recentIds, views]);
  const active = views.find((view) => view.id === activeId);

  function setMenuOpen(next: boolean) {
    setOpen(next);
    if (!next) setQuery("");
  }

  function choose(view: SavedViewRead) {
    const next = [view.id, ...recentIds.filter((id) => id !== view.id)].slice(0, 5);
    setRecentIds(next);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    onSelect(view);
    setMenuOpen(false);
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Localized>
      <>
        <DropdownMenu
          open={open}
          onOpenChange={setMenuOpen}
          role="dialog"
          contentClassName="w-72 rounded-md border border-border bg-popover text-popover-foreground shadow-lg"
          trigger={
            <Button
              type="button"
              size={triggerSize}
              variant={triggerVariant}
              data-menu-trigger
              role={triggerRole}
              aria-haspopup="dialog"
              aria-expanded={open}
              onClick={() => setMenuOpen(!open)}
              className={cn("max-w-44", triggerClassName)}
            >
              <Bookmark className="h-4 w-4" />
              <span className="truncate">{active?.name ?? "Saved views"}</span>
              {active && modified && (
                <span className="text-warning" aria-label="Modified saved view">
                  •
                </span>
              )}
              {views.length > 0 && <span className="text-muted-foreground">{views.length}</span>}
            </Button>
          }
        >
          <div className="border-b border-border p-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Find a saved view..."
                aria-label="Find a saved view"
                className="pl-8"
              />
            </div>
          </div>
          <div className="max-h-64 overflow-y-auto p-1">
            {filtered.length ? (
              filtered.map((view, index) => (
                <div
                  key={view.id}
                  className="group flex items-center rounded hover:bg-popover-hover"
                >
                  <button
                    type="button"
                    onClick={() => choose(view)}
                    className="flex min-w-0 flex-1 items-center gap-2 rounded px-2.5 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="min-w-0 flex-1 truncate">{view.name}</span>
                    {!query && index < recentIds.length && recentIds.includes(view.id) && (
                      <span className="font-mono text-3xs text-muted-foreground">Recent</span>
                    )}
                    {view.id === activeId && <Check className="h-4 w-4 text-primary" />}
                  </button>
                  <div className="flex pr-1 opacity-70 group-hover:opacity-100">
                    <button
                      type="button"
                      title="Update with current filters"
                      aria-label={`Update ${view.name}`}
                      onClick={() => void run(() => onUpdate(view))}
                      className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Rename"
                      aria-label={`Rename ${view.name}`}
                      onClick={() => {
                        setEditing(view);
                        setName(view.name);
                        setMenuOpen(false);
                      }}
                      className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Duplicate"
                      aria-label={`Duplicate ${view.name}`}
                      onClick={() => void run(() => onDuplicate(view))}
                      className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Delete"
                      aria-label={`Delete ${view.name}`}
                      onClick={() => {
                        setDeleting(view);
                        setMenuOpen(false);
                      }}
                      className="rounded p-1.5 text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                {views.length ? "No matching views" : "No saved views yet"}
              </p>
            )}
          </div>
          <div className="border-t border-border p-1">
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onCreate();
              }}
              className="flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-sm font-medium text-foreground transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <BookmarkPlus className="h-4 w-4 text-muted-foreground" /> Save current view
            </button>
          </div>
        </DropdownMenu>
        <Modal
          open={!!editing}
          onClose={() => setEditing(null)}
          title="Rename saved view"
          className="max-w-sm"
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (editing && name.trim())
                void run(async () => {
                  await onRename(editing, name.trim());
                  setEditing(null);
                });
            }}
            className="space-y-4"
          >
            <Input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button type="submit" loading={busy} disabled={!name.trim()}>
                Rename
              </Button>
            </div>
          </form>
        </Modal>
        <ConfirmModal
          open={!!deleting}
          onClose={() => setDeleting(null)}
          onConfirm={() =>
            deleting &&
            void run(async () => {
              await onDelete(deleting);
              setDeleting(null);
            })
          }
          title="Delete saved view?"
          description={
            deleting ? `“${deleting.name}” will be removed. Models are not affected.` : ""
          }
          confirmLabel="Delete"
          busy={busy}
        />
      </>
    </Localized>
  );
}
