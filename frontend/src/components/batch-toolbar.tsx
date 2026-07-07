"use client";

import { useMemo, useState } from "react";
import { FolderInput, Plus, Tag, Trash2, X } from "lucide-react";
import { CollectionRead, TagRead } from "@/types";
import { Modal } from "@/components/ui/modal";
import { ConfirmModal } from "@/components/ui/confirm-modal";

/**
 * Floating action bar shown when one or more models are selected in the grid.
 * Owns its own Move / Tag / Delete dialogs; the parent owns selection state and
 * the actual batch API calls (passed in as handlers).
 */
export function BatchToolbar({
  count,
  collections,
  tags,
  busy,
  onMove,
  onApplyTags,
  onDelete,
  onClear,
}: {
  count: number;
  collections: CollectionRead[];
  tags: TagRead[];
  busy: boolean;
  /** target collection path; "" means move to root */
  onMove: (target: string) => void;
  onApplyTags: (add: string[], remove: string[]) => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (count === 0) return null;

  return (
    <>
      <div className="fixed inset-x-0 bottom-4 z-50 flex justify-center px-4 pointer-events-none">
        <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-background/95 px-3 py-2 shadow-lg backdrop-blur">
          <span className="px-2 font-mono text-xs font-semibold text-foreground">
            {count} selected
          </span>
          <div className="h-5 w-px bg-border" />
          <button
            type="button"
            onClick={() => setMoveOpen(true)}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            <FolderInput className="h-4 w-4 text-muted-foreground" />
            Move
          </button>
          <button
            type="button"
            onClick={() => setTagOpen(true)}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            <Tag className="h-4 w-4 text-muted-foreground" />
            Tag
          </button>
          <button
            type="button"
            onClick={() => setDeleteOpen(true)}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40 transition-colors disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" />
            Delete
          </button>
          <div className="h-5 w-px bg-border" />
          <button
            type="button"
            onClick={onClear}
            disabled={busy}
            className="rounded-full p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors disabled:opacity-50"
            aria-label="Clear selection"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <MoveDialog
        open={moveOpen}
        count={count}
        collections={collections}
        busy={busy}
        onClose={() => setMoveOpen(false)}
        onConfirm={(target) => {
          setMoveOpen(false);
          onMove(target);
        }}
      />

      <TagDialog
        open={tagOpen}
        count={count}
        tags={tags}
        busy={busy}
        onClose={() => setTagOpen(false)}
        onConfirm={(add, remove) => {
          setTagOpen(false);
          onApplyTags(add, remove);
        }}
      />

      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => {
          setDeleteOpen(false);
          onDelete();
        }}
        title={`Delete ${count} model${count !== 1 ? "s" : ""}?`}
        description="They move to the trash and can be restored until purged."
        confirmLabel="Delete"
        busy={busy}
      />
    </>
  );
}

function MoveDialog({
  open,
  count,
  collections,
  busy,
  onClose,
  onConfirm,
}: {
  open: boolean;
  count: number;
  collections: CollectionRead[];
  busy: boolean;
  onClose: () => void;
  onConfirm: (target: string) => void;
}) {
  const [target, setTarget] = useState<string | null>(null);
  const sorted = useMemo(
    () => [...collections].sort((a, b) => a.path.localeCompare(b.path)),
    [collections],
  );

  return (
    <Modal open={open} onClose={onClose} title={`Move ${count} model${count !== 1 ? "s" : ""}`} className="max-w-md">
      <div className="max-h-72 overflow-y-auto rounded border border-border">
        <button
          type="button"
          onClick={() => setTarget("")}
          className={`w-full text-left px-3 py-2 font-mono text-xs transition-colors ${
            target === "" ? "bg-blue-50 text-blue-700 dark:bg-orange-950/40 dark:text-orange-400" : "text-muted-foreground hover:bg-muted"
          }`}
        >
          None (root)
        </button>
        {sorted.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => setTarget(c.path)}
            className={`w-full text-left px-3 py-2 font-mono text-xs transition-colors ${
              target === c.path ? "bg-blue-50 text-blue-700 dark:bg-orange-950/40 dark:text-orange-400" : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {c.path} <span className="opacity-50">({c.model_count})</span>
          </button>
        ))}
      </div>
      <div className="mt-5 flex gap-3">
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          className="flex-1 h-9 rounded border border-border text-sm font-mono uppercase tracking-wider text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => target !== null && onConfirm(target)}
          disabled={busy || target === null}
          className="flex-1 h-9 rounded bg-blue-600 dark:bg-orange-600 text-white text-sm font-mono uppercase tracking-wider hover:bg-blue-700 dark:hover:bg-orange-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Move here
        </button>
      </div>
    </Modal>
  );
}

function TagDialog({
  open,
  count,
  tags,
  busy,
  onClose,
  onConfirm,
}: {
  open: boolean;
  count: number;
  tags: TagRead[];
  busy: boolean;
  onClose: () => void;
  onConfirm: (add: string[], remove: string[]) => void;
}) {
  const [add, setAdd] = useState<string[]>([]);
  const [remove, setRemove] = useState<string[]>([]);

  function reset() {
    setAdd([]);
    setRemove([]);
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title={`Tag ${count} model${count !== 1 ? "s" : ""}`}
      className="max-w-md"
    >
      <div className="space-y-4">
        <ChipEditor
          label="Add tags"
          suggestions={tags}
          values={add}
          onChange={setAdd}
          accent="add"
        />
        <ChipEditor
          label="Remove tags"
          suggestions={tags}
          values={remove}
          onChange={setRemove}
          accent="remove"
        />
      </div>
      <div className="mt-5 flex gap-3">
        <button
          type="button"
          onClick={() => {
            reset();
            onClose();
          }}
          disabled={busy}
          className="flex-1 h-9 rounded border border-border text-sm font-mono uppercase tracking-wider text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => {
            onConfirm(add, remove);
            reset();
          }}
          disabled={busy || (add.length === 0 && remove.length === 0)}
          className="flex-1 h-9 rounded bg-blue-600 dark:bg-orange-600 text-white text-sm font-mono uppercase tracking-wider hover:bg-blue-700 dark:hover:bg-orange-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Apply
        </button>
      </div>
    </Modal>
  );
}

function ChipEditor({
  label,
  suggestions,
  values,
  onChange,
  accent,
}: {
  label: string;
  suggestions: TagRead[];
  values: string[];
  onChange: (next: string[]) => void;
  accent: "add" | "remove";
}) {
  const [input, setInput] = useState("");
  const needle = input.trim().toLowerCase();
  const filtered = needle
    ? suggestions
        .filter((t) => t.name.toLowerCase().includes(needle) && !values.includes(t.name))
        .slice(0, 6)
    : [];
  const canCreate =
    accent === "add" &&
    needle.length > 0 &&
    !suggestions.some((t) => t.name.toLowerCase() === needle) &&
    !values.includes(input.trim());

  function commit(name: string) {
    const v = name.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setInput("");
  }

  const chipClasses =
    accent === "add"
      ? "bg-blue-50 text-blue-700 dark:bg-orange-950/40 dark:text-orange-400"
      : "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-400";

  return (
    <div>
      <label className="block font-mono text-[10px] text-muted-foreground tracking-wider uppercase mb-1.5">
        {label}
      </label>
      <div className="relative">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && input.trim()) {
              e.preventDefault();
              commit(input);
            } else if (e.key === "Backspace" && !input && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          placeholder={accent === "add" ? "Search or create — press Enter" : "Search tags — press Enter"}
          className="w-full h-10 bg-background text-foreground font-mono text-sm border border-border rounded px-3 focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent"
        />
        {input && (filtered.length > 0 || canCreate) && (
          <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-background border border-border rounded shadow-lg py-1 max-h-40 overflow-y-auto">
            {filtered.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => commit(t.name)}
                className="w-full text-left px-3 py-1.5 font-mono text-xs text-muted-foreground hover:bg-muted flex justify-between gap-2"
              >
                <span className="truncate">{t.name}</span>
                <span className="opacity-50">({t.model_count})</span>
              </button>
            ))}
            {canCreate && (
              <button
                type="button"
                onClick={() => commit(input)}
                className="w-full text-left px-3 py-1.5 font-mono text-xs text-blue-600 dark:text-orange-500 hover:bg-muted flex items-center gap-2"
              >
                <Plus className="h-3 w-3" /> Create &quot;{input.trim()}&quot;
              </button>
            )}
          </div>
        )}
      </div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {values.map((name) => (
            <span
              key={name}
              className={`inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider ${chipClasses}`}
            >
              {name}
              <button
                type="button"
                onClick={() => onChange(values.filter((v) => v !== name))}
                aria-label={`Remove ${name}`}
                className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-foreground/10"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
