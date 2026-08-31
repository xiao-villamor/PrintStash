"use client";

import { useMemo, useState } from "react";
import { Plus, Tag, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import { Modal } from "@/components/ui/modal";
import { batchTagModels } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useComboboxNav } from "@/lib/use-combobox-nav";
import type { TagRead } from "@/types";

interface TaggedModel {
  id: number;
  name: string;
  tags: string[];
}

function normalized(name: string): string {
  return name.trim().toLocaleLowerCase();
}

export function ModelTagsDialog({
  model,
  suggestions,
  open,
  onClose,
  onSaved,
}: {
  model: TaggedModel;
  suggestions: TagRead[];
  open: boolean;
  onClose: () => void;
  onSaved: (tags: string[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>(() => [...model.tags]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const needle = normalized(query);
  const selectedNames = useMemo(() => new Set(selected.map(normalized)), [selected]);
  const originalNames = useMemo(() => new Set(model.tags.map(normalized)), [model.tags]);
  const matching = useMemo(
    () =>
      needle
        ? suggestions
            .filter(
              (tag) =>
                normalized(tag.name).includes(needle) && !selectedNames.has(normalized(tag.name)),
            )
            .slice(0, 6)
        : [],
    [needle, selectedNames, suggestions],
  );
  const exactMatch = suggestions.find((tag) => normalized(tag.name) === needle);
  const canCreate = needle.length > 0 && exactMatch === undefined && !selectedNames.has(needle);
  const options = [...matching.map((tag) => tag.name), ...(canCreate ? [query.trim()] : [])];
  const hasChanges =
    originalNames.size !== selectedNames.size ||
    [...originalNames].some((name) => !selectedNames.has(name));

  function reset() {
    setSelected([...model.tags]);
    setQuery("");
  }

  function close() {
    reset();
    onClose();
  }

  function commit(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    const canonical = suggestions.find((tag) => normalized(tag.name) === normalized(trimmed))?.name;
    const name = canonical ?? trimmed;
    if (!selectedNames.has(normalized(name))) setSelected((current) => [...current, name]);
    setQuery("");
  }

  const nav = useComboboxNav(query ? options.length : 0, {
    onSelect: (index) => commit(options[index]),
    onCommitInput: () => commit(exactMatch?.name ?? query),
  });

  async function save() {
    if (!hasChanges || busy) return;
    const add = selected.filter((name) => !originalNames.has(normalized(name)));
    const remove = model.tags.filter((name) => !selectedNames.has(normalized(name)));
    setBusy(true);
    try {
      const result = await batchTagModels([model.id], add, remove);
      if (result.succeeded_count !== 1) {
        throw new Error(result.failed[0]?.reason ?? "Could not update tags");
      }
      onSaved([...selected]);
      toast.success("Tags updated");
      setQuery("");
      onClose();
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Localized>
      <Modal open={open} onClose={close} title="Model tags" className="max-w-md">
        <div className="space-y-5">
          <div className="rounded border border-border bg-muted/40 p-3">
            <div className="flex items-start gap-2.5">
              <Tag className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-foreground">{model.name}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  Choose an existing tag or create a new one to group and find this Model.
                </p>
              </div>
            </div>
          </div>

          <div>
            <label
              htmlFor={`model-tags-${model.id}`}
              className="mb-1.5 block font-mono text-3xs uppercase tracking-wider text-muted-foreground"
            >
              Search or create a tag
            </label>
            <div className="relative">
              <input
                id={`model-tags-${model.id}`}
                value={query}
                maxLength={255}
                placeholder="Type a tag name…"
                onChange={(event) => {
                  setQuery(event.target.value);
                  nav.setActiveIndex(-1);
                }}
                {...nav.inputProps}
                className="h-10 w-full rounded border border-input bg-background px-3 font-mono text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {query && options.length > 0 && (
                <div
                  id={nav.listboxId}
                  role="listbox"
                  className="pop-in absolute left-0 right-0 top-full z-dropdown mt-1 max-h-44 overflow-y-auto rounded border border-border bg-popover py-1 text-popover-foreground shadow-lg"
                >
                  {matching.map((tag, index) => (
                    <button
                      key={tag.id}
                      id={nav.optionId(index)}
                      type="button"
                      role="option"
                      aria-selected={index === nav.activeIndex}
                      onClick={() => commit(tag.name)}
                      className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left font-mono text-xs hover:bg-popover-hover ${index === nav.activeIndex ? "bg-popover-hover" : ""}`}
                    >
                      <span className="truncate">{tag.name}</span>
                      <span className="text-muted-foreground">{tag.model_count}</span>
                    </button>
                  ))}
                  {canCreate && (
                    <button
                      id={nav.optionId(matching.length)}
                      type="button"
                      role="option"
                      aria-selected={matching.length === nav.activeIndex}
                      onClick={() => commit(query)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-xs text-primary hover:bg-popover-hover ${matching.length === nav.activeIndex ? "bg-popover-hover" : ""}`}
                    >
                      <Plus className="h-3.5 w-3.5" /> Create tag &quot;{query.trim()}&quot;
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>

          <div>
            <p className="mb-2 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
              Assigned tags
            </p>
            {selected.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {selected.map((name) => (
                  <span
                    key={normalized(name)}
                    className="inline-flex items-center gap-1 rounded border border-primary-soft bg-accent py-1 pl-2.5 pr-1.5 font-mono text-xs font-semibold text-accent-foreground"
                  >
                    {name}
                    <button
                      type="button"
                      onClick={() =>
                        setSelected((current) =>
                          current.filter((tag) => normalized(tag) !== normalized(name)),
                        )
                      }
                      aria-label={`Remove ${name}`}
                      className="flex h-5 w-5 items-center justify-center rounded-sm text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="rounded border border-dashed border-border px-3 py-5 text-center text-sm text-muted-foreground">
                No tags assigned yet.
              </p>
            )}
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={close}>
            Cancel
          </Button>
          <Button type="button" size="sm" loading={busy} disabled={!hasChanges} onClick={save}>
            Save tags
          </Button>
        </div>
      </Modal>
    </Localized>
  );
}
