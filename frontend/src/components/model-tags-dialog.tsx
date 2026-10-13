"use client";

import { captureEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Plus, Tag, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import { Modal } from "@/components/ui/modal";
import { getSessionVersion } from "@/lib/session-transport";
import { batchTagModels, getModel } from "@/lib/api/models";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { useComboboxNav } from "@/lib/use-combobox-nav";
import type { ModelRead, TagRead } from "@/types";

interface TaggedModel extends EditingBase {
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
  onSaved: (tags: string[], base: EditingBase) => void;
}) {
  useUiLocale();
  const [base, setBase] = useState(model);
  const [selected, setSelected] = useState<string[]>(() => [...model.tags]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const mounted = useRef(true);
  const pending = useRef(false);
  const intent = useRef(0);
  const reviewRequest = useRef<AbortController | null>(null);
  const [problem, setProblem] = useState<"conflict" | "unconfirmed" | null>(null);
  const [reviewed, setReviewed] = useState<ModelRead | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [editDenied, setEditDenied] = useState(false);
  const [readDenied, setReadDenied] = useState(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      intent.current += 1;
      reviewRequest.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!open || session !== currentSession) {
      intent.current += 1;
      reviewRequest.current?.abort();
    }
  }, [open, session, currentSession]);
  const isCurrent = (generation: number) =>
    mounted.current && open && generation === intent.current && session === getSessionVersion();
  const needle = normalized(query);
  const selectedNames = useMemo(() => new Set(selected.map(normalized)), [selected]);
  const originalNames = useMemo(() => new Set(base.tags.map(normalized)), [base.tags]);
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
    setBase(model);
    setSelected([...model.tags]);
    setQuery("");
    setProblem(null);
    setReviewed(null);
    setEditDenied(false);
    setReadDenied(false);
    setReviewing(false);
  }

  function close() {
    if (pending.current) return;
    intent.current += 1;
    reviewRequest.current?.abort();
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

  async function save(reviewBase?: ModelRead) {
    if (pending.current || editDenied || session !== getSessionVersion()) return;
    if (!reviewBase && (!hasChanges || problem)) return;
    if (reviewBase && reviewBase.effective_role !== "edit" && reviewBase.effective_role !== "admin")
      return;
    const capturedBase = reviewBase ?? base;
    const capturedNames = new Set(capturedBase.tags.map(normalized));
    const submitted = [...selected];
    const add = submitted.filter((name) => !capturedNames.has(normalized(name)));
    const remove = capturedBase.tags.filter((name) => !selectedNames.has(normalized(name)));
    const generation = intent.current;
    pending.current = true;
    setBusy(true);
    try {
      const result = await batchTagModels([capturedBase.id], add, remove, {
        [capturedBase.id]: captureEditingBase(capturedBase),
      });
      if (!isCurrent(generation)) return;
      if (
        result.failed.some(
          (failure) => failure.model_id === capturedBase.id && failure.reason === "edit_conflict",
        )
      ) {
        setProblem("conflict");
        setReviewed(null);
        return;
      }
      if (result.succeeded_count !== 1 || result.succeeded_ids[0] !== capturedBase.id) {
        throw new Error(result.failed[0]?.reason ?? "invalid_edit_acknowledgment");
      }
      const version = result.succeeded_versions[capturedBase.id];
      requireEditingReceipt(version, capturedBase);
      onSaved(submitted, version);
      toast.success(uiText("Tags updated"));
      setQuery("");
      setProblem(null);
      setReviewed(null);
      onClose();
    } catch (error) {
      if (!isCurrent(generation)) return;
      if (error instanceof ApiError && error.status === 412) {
        setProblem("conflict");
        setReviewed(null);
      } else if (
        !(error instanceof ApiError) ||
        error.status === 0 ||
        error.status === 408 ||
        error.status >= 500
      ) {
        setProblem("unconfirmed");
        setReviewed(null);
      } else toast.error(error);
    } finally {
      pending.current = false;
      if (isCurrent(generation)) setBusy(false);
    }
  }

  async function reviewLatest() {
    if (reviewing || pending.current || session !== getSessionVersion()) return;
    const generation = intent.current;
    const controller = new AbortController();
    reviewRequest.current?.abort();
    reviewRequest.current = controller;
    setReviewing(true);
    try {
      const latest = await getModel(model.id, { signal: controller.signal });
      if (!isCurrent(generation) || controller.signal.aborted) return;
      setReadDenied(false);
      setReviewed(latest);
      setEditDenied(latest.effective_role !== "edit" && latest.effective_role !== "admin");
    } catch (error) {
      if (!isCurrent(generation) || controller.signal.aborted) return;
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setReadDenied(true);
        setReviewed(null);
      } else toast.error(error);
    } finally {
      if (isCurrent(generation) && !controller.signal.aborted) setReviewing(false);
    }
  }

  function useLatest() {
    if (!reviewed) return;
    setBase(reviewed);
    setSelected([...reviewed.tags]);
    setQuery("");
    setProblem(null);
    setReviewed(null);
  }

  if (session !== currentSession) return null;
  if (readDenied)
    return (
      <Modal open={open} onClose={close} title={uiText("Model tags")} className="max-w-md">
        <div role="alert" className="space-y-3">
          <p>{uiText("Couldn’t load this model")}</p>
          <Button variant="outline" loading={reviewing} onClick={() => void reviewLatest()}>
            {uiText("Retry")}
          </Button>
        </div>
      </Modal>
    );

  return (
    <Localized>
      <Modal open={open} onClose={close} title={uiText("Model tags")} className="max-w-md">
        {problem && (
          <div
            role="alert"
            className="mb-4 space-y-3 rounded border border-border bg-muted p-3 text-sm"
          >
            <p>
              {uiText(problem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed")}
            </p>
            <Button
              variant="outline"
              size="sm"
              loading={reviewing}
              disabled={busy}
              onClick={() => void reviewLatest()}
            >
              {uiText("library.reviewLatest")}
            </Button>
            {reviewed && (
              <section aria-label={uiText("library.latestVersion")} className="space-y-3">
                <h3 className="font-semibold">{uiText("library.latestVersion")}</h3>
                <p>{reviewed.tags.join(", ") || uiText("No tags assigned yet.")}</p>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" disabled={busy} onClick={useLatest}>
                    {uiText("library.useLatest")}
                  </Button>
                  <Button
                    size="sm"
                    loading={busy}
                    disabled={editDenied || reviewing}
                    onClick={() => void save(reviewed)}
                  >
                    {uiText("library.retryDraft")}
                  </Button>
                </div>
              </section>
            )}
          </div>
        )}
        <div className="space-y-5">
          <div className="rounded border border-border bg-muted/40 p-3">
            <div className="flex items-start gap-2.5">
              <Tag className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-foreground">{model.name}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  {uiText(
                    "Choose an existing tag or create a new one to group and find this Model.",
                  )}
                </p>
              </div>
            </div>
          </div>

          <div>
            <label
              htmlFor={`model-tags-${model.id}`}
              className="mb-1.5 block font-mono text-3xs uppercase tracking-wider text-muted-foreground"
            >
              {uiText("Search or create a tag")}
            </label>
            <div className="relative">
              <input
                id={`model-tags-${model.id}`}
                value={query}
                maxLength={255}
                disabled={busy || editDenied}
                placeholder={uiText("Type a tag name…")}
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
                      <Plus className="h-3.5 w-3.5" />
                      {uiText(' Create tag "')}
                      {query.trim()}
                      {uiText('"')}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>

          <div>
            <p className="mb-2 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
              {uiText("Assigned tags")}
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
                      disabled={busy || editDenied}
                      onClick={() =>
                        setSelected((current) =>
                          current.filter((tag) => normalized(tag) !== normalized(name)),
                        )
                      }
                      aria-label={uiText("Remove {value1}", { value1: String(name) })}
                      className="flex h-5 w-5 items-center justify-center rounded-sm text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="rounded border border-dashed border-border px-3 py-5 text-center text-sm text-muted-foreground">
                {uiText("No tags assigned yet.")}
              </p>
            )}
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={close}>
            {uiText("Cancel")}
          </Button>
          <Button
            type="button"
            size="sm"
            loading={busy}
            disabled={!hasChanges || problem !== null || editDenied || reviewing}
            onClick={() => void save()}
          >
            {uiText("Save tags")}
          </Button>
        </div>
      </Modal>
    </Localized>
  );
}
