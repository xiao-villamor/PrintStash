"use client";

import { captureEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
      <Modal open={open} onClose={close} title={uiText("Edit Model tags")} className="max-w-md">
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
      <Modal
        open={open}
        onClose={close}
        title={uiText("Edit Model tags")}
        className="max-w-md"
        description={<span className="wrap-anywhere">{model.name}</span>}
        footer={
          <>
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
          </>
        }
      >
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
        <div className="space-y-3">
          <div>
            <label
              htmlFor={`model-tags-${model.id}`}
              className="mb-1.5 block text-field font-medium text-foreground"
            >
              {uiText("Find or create tag")}
            </label>
            <div className="relative">
              <Input
                data-autofocus
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
              />
              {query && options.length > 0 && (
                <div
                  id={nav.listboxId}
                  role="listbox"
                  className="mt-1 max-h-40 divide-y divide-border overflow-y-auto border border-border"
                >
                  {matching.map((tag, index) => (
                    <button
                      key={tag.id}
                      id={nav.optionId(index)}
                      type="button"
                      role="option"
                      aria-selected={index === nav.activeIndex}
                      onClick={() => commit(tag.name)}
                      className={`flex w-full items-center justify-between gap-2 px-2 py-2 text-left text-sm hover:bg-popover-hover ${index === nav.activeIndex ? "bg-popover-hover" : ""}`}
                    >
                      <span className="truncate">{tag.name}</span>
                      <span className="text-muted-foreground">
                        {tag.model_count} {uiText("Models")}
                      </span>
                    </button>
                  ))}
                  {canCreate && (
                    <button
                      id={nav.optionId(matching.length)}
                      type="button"
                      role="option"
                      aria-selected={matching.length === nav.activeIndex}
                      onClick={() => commit(query)}
                      className={`flex w-full items-center gap-2 px-2 py-2 text-left text-sm text-primary hover:bg-popover-hover ${matching.length === nav.activeIndex ? "bg-popover-hover" : ""}`}
                    >
                      <Plus className="size-3.5 shrink-0" aria-hidden="true" />
                      <span className="min-w-0 wrap-anywhere">
                        {uiText(' Create tag "')}
                        {query.trim()}
                        {uiText('"')}
                      </span>
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
          {selected.length > 0 && (
            <div role="group" aria-label={uiText("Tags")} className="flex flex-wrap gap-1.5">
              {selected.map((name) => (
                <span
                  key={normalized(name)}
                  className="inline-flex min-w-0 items-center gap-1 rounded-sm bg-accent py-0.5 pl-2 pr-1 text-sm text-accent-foreground"
                >
                  <span className="min-w-0 wrap-anywhere">{name}</span>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    className="size-6 shrink-0"
                    disabled={busy || editDenied}
                    onClick={() =>
                      setSelected((current) =>
                        current.filter((tag) => normalized(tag) !== normalized(name)),
                      )
                    }
                    aria-label={uiText("Remove {value1}", { value1: name })}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                  </Button>
                </span>
              ))}
            </div>
          )}
        </div>
      </Modal>
    </Localized>
  );
}
