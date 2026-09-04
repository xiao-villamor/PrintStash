"use client";

import { useMemo, useState } from "react";
import { FolderInput, Pencil, Plus, Tag, Trash2, X } from "lucide-react";
import { CollectionRead, TagRead } from "@/types";
import { Modal } from "@/components/ui/modal";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input } from "@/components/ui/input";
import { useComboboxNav } from "@/lib/use-combobox-nav";
import { DURATION, useMountTransition } from "@/lib/overlay";

/**
 * Floating action bar shown when one or more models are selected in the grid.
 * Owns its own Move / Tag / Delete dialogs; the parent owns selection state and
 * the actual batch API calls (passed in as handlers).
 */
export function BatchToolbar({
  modelCount,
  selectedCollections,
  collections,
  tags,
  busy,
  canMoveToRoot = true,
  onMoveSelection,
  onRenameCollections,
  onApplyTags,
  onDeleteSelection,
  onClear,
}: {
  modelCount: number;
  selectedCollections: CollectionRead[];
  collections: CollectionRead[];
  tags: TagRead[];
  busy: boolean;
  canMoveToRoot?: boolean;
  /** target collection path; "" means move to root */
  onMoveSelection: (target: string, parentId: number | null) => void;
  onRenameCollections: (names: Record<number, string>) => void;
  onApplyTags: (add: string[], remove: string[]) => void;
  onDeleteSelection: () => void;
  onClear: () => void;
}) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  // Must match the pill's `duration-fast` transition below.
  const count = modelCount + selectedCollections.length;
  const { mounted, state } = useMountTransition(count > 0, DURATION.fast);

  if (!mounted) return null;

  return (
    <>
      <div className="fixed inset-x-0 bottom-4 z-50 flex justify-center px-4 pointer-events-none">
        <div
          data-state={state}
          className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-background/95 px-3 py-2 shadow-lg backdrop-blur transition-[opacity,transform] duration-fast ease-out data-[state=closed]:translate-y-2 data-[state=closed]:opacity-0 motion-reduce:data-[state=closed]:translate-y-0"
        >
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
          {modelCount > 0 && selectedCollections.length === 0 && (
            <button
              type="button"
              onClick={() => setTagOpen(true)}
              disabled={busy}
              className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
            >
              <Tag className="h-4 w-4 text-muted-foreground" />
              Tag
            </button>
          )}
          {selectedCollections.length > 0 && (
            <button
              type="button"
              onClick={() => setRenameOpen(true)}
              disabled={busy}
              className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
            >
              <Pencil className="h-4 w-4 text-muted-foreground" /> Rename
            </button>
          )}
          <button
            type="button"
            onClick={() => setDeleteOpen(true)}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50"
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
        selectedCollections={selectedCollections}
        busy={busy}
        canMoveToRoot={canMoveToRoot}
        onClose={() => setMoveOpen(false)}
        onConfirm={(target, parentId) => {
          setMoveOpen(false);
          onMoveSelection(target, parentId);
        }}
      />

      {modelCount > 0 && selectedCollections.length === 0 && (
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
      )}

      <RenameCollectionsDialog
        open={renameOpen}
        collections={selectedCollections}
        busy={busy}
        onClose={() => setRenameOpen(false)}
        onConfirm={(names) => {
          setRenameOpen(false);
          onRenameCollections(names);
        }}
      />

      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => {
          setDeleteOpen(false);
          onDeleteSelection();
        }}
        title={`Delete ${count} selected item${count !== 1 ? "s" : ""}?`}
        description={
          selectedCollections.length
            ? "Selected folders and their contents move to trash."
            : "They move to trash and can be restored until purged."
        }
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
  selectedCollections,
  busy,
  canMoveToRoot,
  onClose,
  onConfirm,
}: {
  open: boolean;
  count: number;
  collections: CollectionRead[];
  selectedCollections: CollectionRead[];
  busy: boolean;
  canMoveToRoot: boolean;
  onClose: () => void;
  onConfirm: (target: string, parentId: number | null) => void;
}) {
  const [target, setTarget] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const blockedPaths = useMemo(
    () => selectedCollections.map((collection) => collection.path),
    [selectedCollections],
  );
  const sorted = useMemo(
    () =>
      collections
        .filter(
          (collection) =>
            !blockedPaths.some(
              (path) => collection.path === path || collection.path.startsWith(`${path}/`),
            ),
        )
        .filter((collection) => collection.path.toLowerCase().includes(query.trim().toLowerCase()))
        .sort((a, b) => a.path.localeCompare(b.path)),
    [blockedPaths, collections, query],
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Move ${count} item${count !== 1 ? "s" : ""}`}
      className="max-w-md"
    >
      <Input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Find destination..."
        aria-label="Find destination"
        className="mb-2"
      />
      <div className="max-h-72 overflow-y-auto rounded border border-border">
        {canMoveToRoot && (
          <button
            type="button"
            onClick={() => setTarget("")}
            className={`w-full text-left px-3 py-2 font-mono text-xs transition-colors ${
              target === ""
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            None (root)
          </button>
        )}
        {sorted.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => setTarget(c.path)}
            className={`w-full text-left px-3 py-2 font-mono text-xs transition-colors ${
              target === c.path
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {c.path} <span className="opacity-50">({c.model_count})</span>
          </button>
        ))}
        {sorted.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-muted-foreground">
            No valid destinations
          </p>
        )}
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
          onClick={() => {
            if (target === null) return;
            const parentId =
              target === ""
                ? null
                : (collections.find((collection) => collection.path === target)?.id ?? null);
            onConfirm(target, parentId);
          }}
          disabled={busy || target === null}
          className="flex-1 h-9 rounded bg-primary text-primary-foreground text-sm font-mono uppercase tracking-wider hover:bg-primary-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Move here
        </button>
      </div>
    </Modal>
  );
}

function RenameCollectionsDialog({
  open,
  collections,
  busy,
  onClose,
  onConfirm,
}: {
  open: boolean;
  collections: CollectionRead[];
  busy: boolean;
  onClose: () => void;
  onConfirm: (names: Record<number, string>) => void;
}) {
  const [names, setNames] = useState<Record<number, string>>({});
  const values = Object.fromEntries(
    collections.map((collection) => [collection.id, names[collection.id] ?? collection.name]),
  );
  const valid =
    collections.length > 0 && collections.every((collection) => values[collection.id].trim());

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Rename ${collections.length} folder${collections.length !== 1 ? "s" : ""}`}
      className="max-w-md"
    >
      <div className="max-h-72 space-y-3 overflow-y-auto pr-1">
        {collections.map((collection) => (
          <label key={collection.id} className="block space-y-1">
            <span className="block truncate font-mono text-3xs text-muted-foreground">
              {collection.path}
            </span>
            <input
              value={values[collection.id]}
              onChange={(event) =>
                setNames((current) => ({ ...current, [collection.id]: event.target.value }))
              }
              maxLength={100}
              className="h-9 w-full rounded border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </label>
        ))}
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          className="h-9 rounded border border-border px-4 text-sm text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onConfirm(values)}
          disabled={busy || !valid}
          className="h-9 rounded bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary-hover disabled:opacity-50"
        >
          Rename
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
          className="flex-1 h-9 rounded bg-primary text-primary-foreground text-sm font-mono uppercase tracking-wider hover:bg-primary-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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

  const items = [...filtered.map((t) => t.name), ...(canCreate ? [input.trim()] : [])];
  const nav = useComboboxNav(input ? items.length : 0, {
    onSelect: (i) => commit(items[i]),
    onCommitInput: () => {
      if (input.trim()) commit(input);
    },
  });

  const chipClasses =
    accent === "add" ? "bg-accent text-accent-foreground" : "bg-destructive/10 text-destructive";

  return (
    <div>
      <label className="block font-mono text-3xs text-muted-foreground tracking-wider uppercase mb-1.5">
        {label}
      </label>
      <div className="relative">
        <input
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            nav.setActiveIndex(-1);
          }}
          {...nav.inputProps}
          onKeyDown={(e) => {
            nav.inputProps.onKeyDown(e);
            if (e.defaultPrevented) return;
            if (e.key === "Backspace" && !input && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          placeholder={
            accent === "add" ? "Search or create — press Enter" : "Search tags — press Enter"
          }
          className="w-full h-10 bg-background text-foreground font-mono text-sm border border-border rounded px-3 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
        />
        {input && (filtered.length > 0 || canCreate) && (
          <div
            id={nav.listboxId}
            role="listbox"
            className="pop-in absolute left-0 right-0 top-full mt-1 z-dropdown bg-background border border-border rounded shadow-lg py-1 max-h-40 overflow-y-auto"
          >
            {filtered.map((t, i) => (
              <button
                key={t.id}
                id={nav.optionId(i)}
                role="option"
                aria-selected={i === nav.activeIndex}
                type="button"
                onClick={() => commit(t.name)}
                className={`w-full text-left px-3 py-1.5 font-mono text-xs text-muted-foreground hover:bg-muted flex justify-between gap-2 ${i === nav.activeIndex ? "bg-muted" : ""}`}
              >
                <span className="truncate">{t.name}</span>
                <span className="opacity-50">({t.model_count})</span>
              </button>
            ))}
            {canCreate && (
              <button
                type="button"
                id={nav.optionId(filtered.length)}
                role="option"
                aria-selected={filtered.length === nav.activeIndex}
                onClick={() => commit(input)}
                className={`w-full text-left px-3 py-1.5 font-mono text-xs text-primary hover:bg-muted flex items-center gap-2 ${filtered.length === nav.activeIndex ? "bg-muted" : ""}`}
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
              className={`inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded font-mono text-3xs uppercase tracking-wider ${chipClasses}`}
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
