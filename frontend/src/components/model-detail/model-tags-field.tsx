"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, Pencil, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { captureEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import { batchTagModels, getModel } from "@/lib/api/models";
import { ApiError } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { getSessionVersion } from "@/lib/session-transport";
import { useComboboxNav } from "@/lib/use-combobox-nav";
import type { EditingBase } from "@/types/editing";
import type { ModelRead, TagRead } from "@/types";

function normalized(name: string): string {
  return name.trim().toLocaleLowerCase();
}

/** Tags confirmed by the server, with the editing base that confirmed them. */
interface ConfirmedTags {
  tags: string[];
  base: EditingBase;
}

type TagProblem = "conflict" | "unconfirmed" | "rejected";

/**
 * Model tags edited in place from an anchored panel. Every add or remove is
 * saved at once; changes made while a save is in flight are coalesced into the
 * next request against the newest confirmed version, so none is lost.
 */
export function ModelTagsField({
  model,
  suggestions,
  canEdit,
  onSaved,
}: {
  model: ModelRead;
  suggestions: TagRead[];
  canEdit: boolean;
  onSaved: (tags: string[], base: EditingBase) => void;
}) {
  useUiLocale();
  const inputId = useId();
  const statusId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [desired, setDesired] = useState<string[]>(() => [...model.tags]);
  const [problem, setProblem] = useState<TagProblem | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [savedOnce, setSavedOnce] = useState(false);
  const confirmed = useRef<ConfirmedTags>({
    tags: [...model.tags],
    base: captureEditingBase(model),
  });
  const desiredRef = useRef(desired);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Another writer published a newer Model while nothing here is pending: adopt it.
  const modelKey = `${model.edit_epoch}:${model.edit_version}:${model.tags.join("\u0000")}`;
  const [seenModelKey, setSeenModelKey] = useState(modelKey);
  if (modelKey !== seenModelKey) {
    setSeenModelKey(modelKey);
    if (
      !inFlight.current &&
      problem === null &&
      model.edit_epoch === confirmed.current.base.edit_epoch &&
      model.edit_version >= confirmed.current.base.edit_version
    ) {
      confirmed.current = { tags: [...model.tags], base: captureEditingBase(model) };
      desiredRef.current = [...model.tags];
      setDesired([...model.tags]);
    }
  }

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  async function sync() {
    if (inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    const session = getSessionVersion();
    try {
      for (;;) {
        const current = confirmed.current;
        const target = desiredRef.current;
        const have = new Set(current.tags.map(normalized));
        const want = new Set(target.map(normalized));
        const add = target.filter((name) => !have.has(normalized(name)));
        const remove = current.tags.filter((name) => !want.has(normalized(name)));
        if (add.length === 0 && remove.length === 0) return;
        const result = await batchTagModels([model.id], add, remove, {
          [model.id]: current.base,
        });
        if (session !== getSessionVersion()) return;
        if (
          result.failed.some(
            (failure) => failure.model_id === model.id && failure.reason === "edit_conflict",
          )
        ) {
          if (mounted.current) setProblem("conflict");
          return;
        }
        if (result.succeeded_count !== 1 || result.succeeded_ids[0] !== model.id)
          throw new Error(result.failed[0]?.reason ?? "invalid_edit_acknowledgment");
        const version = result.succeeded_versions[model.id];
        requireEditingReceipt(version, current.base);
        confirmed.current = { tags: [...target], base: captureEditingBase(version) };
        onSaved([...target], captureEditingBase(version));
        if (mounted.current) setSavedOnce(true);
      }
    } catch (error) {
      if (!mounted.current || session !== getSessionVersion()) return;
      if (error instanceof ApiError && error.status === 412) setProblem("conflict");
      else if (
        !(error instanceof ApiError) ||
        error.status === 0 ||
        error.status === 408 ||
        error.status >= 500
      )
        setProblem("unconfirmed");
      else setProblem("rejected");
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  function change(next: string[]) {
    desiredRef.current = next;
    setDesired(next);
    if (problem === null) void sync();
  }

  /**
   * Read the newest Model. With `keepMine`, the additions and removals this
   * person made since the last confirmed save are replayed on top of it, so a
   * concurrent writer's tags survive; otherwise the newest tags replace the draft.
   */
  async function reloadLatest(keepMine: boolean) {
    setReviewing(true);
    try {
      const latest = await getModel(model.id);
      if (!mounted.current) return;
      const before = new Set(confirmed.current.tags.map(normalized));
      const mine = new Set(desiredRef.current.map(normalized));
      const added = desiredRef.current.filter((name) => !before.has(normalized(name)));
      const removed = new Set([...before].filter((name) => !mine.has(name)));
      confirmed.current = { tags: [...latest.tags], base: captureEditingBase(latest) };
      onSaved([...latest.tags], captureEditingBase(latest));
      const next = keepMine
        ? [
            ...latest.tags.filter((name) => !removed.has(normalized(name))),
            ...added.filter(
              (name) => !latest.tags.some((tag) => normalized(tag) === normalized(name)),
            ),
          ]
        : [...latest.tags];
      desiredRef.current = next;
      setDesired(next);
      setProblem(null);
      if (keepMine) void sync();
    } catch {
      if (mounted.current) setProblem("unconfirmed");
    } finally {
      if (mounted.current) setReviewing(false);
    }
  }

  function retry() {
    if (problem === "conflict" || problem === "unconfirmed") {
      void reloadLatest(true);
      return;
    }
    setProblem(null);
    void sync();
  }

  function discard() {
    void reloadLatest(false);
  }

  const selectedNames = useMemo(() => new Set(desired.map(normalized)), [desired]);
  const needle = normalized(query);
  const matching = suggestions.filter(
    (tag) => needle === "" || normalized(tag.name).includes(needle),
  );
  const exact = suggestions.find((tag) => normalized(tag.name) === needle);
  const canCreate = needle.length > 0 && exact === undefined && !selectedNames.has(needle);
  const options = [...matching.map((tag) => tag.name), ...(canCreate ? [query.trim()] : [])];

  function add(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    const name =
      suggestions.find((tag) => normalized(tag.name) === normalized(trimmed))?.name ?? trimmed;
    setQuery("");
    nav.setActiveIndex(-1);
    if (selectedNames.has(normalized(name))) {
      remove(name);
      return;
    }
    change([...desiredRef.current, name]);
  }

  function remove(name: string, focusAfter?: HTMLElement | null) {
    change(desiredRef.current.filter((tag) => normalized(tag) !== normalized(name)));
    (focusAfter ?? input.current)?.focus();
  }

  const nav = useComboboxNav(options.length, {
    onSelect: (index) => add(options[index]),
    onCommitInput: () => add(exact?.name ?? query),
  });

  const shown = open || problem !== null || saving ? desired : model.tags;
  const label = shown.length
    ? uiText("Edit tags for {value1}", { value1: model.name })
    : uiText("Add tags to {value1}", { value1: model.name });

  const problemText =
    problem === "conflict"
      ? uiText("library.editConflict")
      : problem === "unconfirmed"
        ? uiText("library.saveUnconfirmed")
        : problem === "rejected"
          ? uiText("modelDetail.tagsNotSaved")
          : null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {!canEdit && shown.length === 0 && (
        <span className="text-sm text-muted-foreground">{uiText("None")}</span>
      )}
      {shown.map((tag) => (
        <span
          className="min-w-0 wrap-anywhere bg-muted px-2 py-1 text-sm text-foreground"
          key={normalized(tag)}
        >
          {tag}
        </span>
      ))}
      {canEdit && (
        <DropdownMenu
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) setQuery("");
          }}
          role="dialog"
          aria-label={uiText("Edit Model tags")}
          align="start"
          portal
          contentClassName="w-80 border border-border bg-popover p-3 text-popover-foreground shadow-lg"
          trigger={
            <Button
              type="button"
              size="sm"
              variant="ghost"
              data-menu-trigger
              aria-haspopup="dialog"
              aria-expanded={open}
              aria-label={label}
              title={label}
              className={` gap-1.5 border border-dotted border-muted-foreground/70 px-2 text-muted-foreground hover:border-foreground hover:text-foreground ${shown.length ? "w-8 px-0" : ""}`}
              onClick={() => setOpen((value) => !value)}
            >
              <Pencil className="size-3.5" aria-hidden />
              {shown.length === 0 && <span className="text-sm">{uiText("Add tags")}</span>}
            </Button>
          }
        >
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor={inputId} className="text-sm font-medium">
                {uiText("Find or create tag")}
              </label>
              <span id={statusId} role="status" className="text-sm text-muted-foreground">
                {saving || reviewing
                  ? uiText("Saving…")
                  : problem === null && savedOnce
                    ? uiText("Saved")
                    : ""}
              </span>
            </div>
            <Input
              ref={input}
              id={inputId}
              value={query}
              maxLength={255}
              placeholder={uiText("Type a tag name…")}
              aria-describedby={statusId}
              onChange={(event) => {
                setQuery(event.target.value);
                nav.setActiveIndex(-1);
              }}
              {...nav.inputProps}
            />
            {needle && matching.length === 0 && (
              <p className="text-sm text-muted-foreground">{uiText("No matching tags.")}</p>
            )}
            {options.length > 0 && (
              <div
                id={nav.listboxId}
                role="listbox"
                aria-multiselectable="true"
                aria-label={uiText("Tags")}
                className="max-h-48 divide-y divide-border overflow-y-auto border border-border"
              >
                {matching.map((tag, index) => (
                  <button
                    key={tag.id}
                    id={nav.optionId(index)}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={selectedNames.has(normalized(tag.name))}
                    onClick={() => {
                      add(tag.name);
                      input.current?.focus();
                    }}
                    className={`flex w-full items-center justify-between gap-2 px-2 py-2 text-left text-sm hover:bg-popover-hover ${index === nav.activeIndex ? "bg-popover-hover" : ""}`}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        className="grid size-4 shrink-0 place-items-center border border-border"
                        aria-hidden
                      >
                        {selectedNames.has(normalized(tag.name)) && <Check className="size-3" />}
                      </span>
                      <span className="truncate">{tag.name}</span>
                    </span>
                    <span className="text-muted-foreground">{tag.model_count}</span>
                  </button>
                ))}
                {canCreate && (
                  <button
                    id={nav.optionId(matching.length)}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={matching.length === nav.activeIndex}
                    onClick={() => {
                      add(query);
                      input.current?.focus();
                    }}
                    className={`flex w-full items-center gap-2 px-2 py-2 text-left text-sm hover:bg-popover-hover ${matching.length === nav.activeIndex ? "bg-popover-hover" : ""}`}
                  >
                    <Plus className="size-3.5 shrink-0" aria-hidden />
                    <span className="min-w-0 wrap-anywhere">
                      {uiText("modelDetail.createTag", { value1: query.trim() })}
                    </span>
                  </button>
                )}
              </div>
            )}
            {desired.length > 0 ? (
              <ul aria-label={uiText("Tags")} className="flex flex-wrap gap-1.5">
                {desired.map((name) => (
                  <li
                    key={normalized(name)}
                    className="inline-flex min-w-0 items-center gap-1 bg-accent py-0.5 pl-2 pr-1 text-sm text-accent-foreground"
                  >
                    <span className="min-w-0 wrap-anywhere">{name}</span>
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="ghost"
                      className="size-6 shrink-0"
                      onClick={(event) => {
                        const item = event.currentTarget.closest("li");
                        const next =
                          item?.nextElementSibling?.querySelector("button") ??
                          item?.previousElementSibling?.querySelector("button");
                        remove(name, next instanceof HTMLElement ? next : null);
                      }}
                      aria-label={uiText("Remove {value1}", { value1: name })}
                    >
                      <X className="size-3.5" aria-hidden />
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{uiText("No tags assigned yet.")}</p>
            )}
            {problemText && (
              <div role="alert" className="space-y-2 border border-border bg-muted p-2 text-sm">
                <p>{problemText}</p>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" loading={reviewing} onClick={retry}>
                    {uiText("Retry")}
                  </Button>
                  <Button size="sm" variant="outline" disabled={reviewing} onClick={discard}>
                    {uiText("modelDetail.discardTagChanges")}
                  </Button>
                </div>
              </div>
            )}
          </div>
        </DropdownMenu>
      )}
    </div>
  );
}
