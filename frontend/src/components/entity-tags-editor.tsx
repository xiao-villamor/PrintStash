import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { X } from "lucide-react";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { TagRead } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

import { ApiError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { getSessionVersion } from "@/lib/session-transport";
import { onAuthChange } from "@/lib/auth-store";

/** The owner binds these commands to the authorized snapshot the user reviews. */
export interface TagsReview {
  tags: string[];
  canEdit: boolean;
  save: (tags: string[], signal: AbortSignal) => Promise<void>;
  adopt: (signal: AbortSignal) => Promise<void>;
}

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase();
}

export function EntityTagsEditor({
  entityLabel,
  tags,
  availableTags,
  help,
  onSave,
  reviewLatest,
  open,
  onClose,
}: {
  entityLabel: string;
  tags: string[];
  availableTags: TagRead[];
  help: string;
  onSave: (tags: string[], signal: AbortSignal) => Promise<void>;
  reviewLatest?: (signal: AbortSignal) => Promise<TagsReview>;
  open: boolean;
  onClose: () => void;
}) {
  useUiLocale();
  const [selected, setSelected] = useState<string[]>(tags);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<"conflict" | "unconfirmed" | null>(null);
  const [reviewed, setReviewed] = useState<TagsReview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [denied, setDenied] = useState(false);
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const mounted = useRef(true);
  const request = useRef<AbortController | null>(null);
  const pending = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      request.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!open || currentSession !== session) request.current?.abort();
  }, [open, currentSession, session]);
  const isCurrent = (controller: AbortController) =>
    mounted.current && !controller.signal.aborted && open && session === getSessionVersion();

  function close() {
    if (pending.current) return;
    request.current?.abort();
    onClose();
  }

  const suggestions = useMemo(() => {
    const needle = normalized(input);
    const chosen = new Set(selected.map(normalized));
    return availableTags.filter(
      (tag) =>
        !chosen.has(normalized(tag.name)) && (!needle || normalized(tag.name).includes(needle)),
    );
  }, [availableTags, input, selected]);

  function add(value: string) {
    const name = value.trim();
    if (!name || selected.some((tag) => normalized(tag) === normalized(name))) return;
    setSelected((current) => [...current, name]);
    setInput("");
  }

  async function submit(review?: TagsReview) {
    if (pending.current || reviewing || denied || session !== getSessionVersion()) return;
    if (review ? !review.canEdit : problem !== null) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    pending.current = true;
    setSaving(true);
    try {
      await (review?.save ?? onSave)([...selected], controller.signal);
      if (isCurrent(controller)) onClose();
    } catch (cause) {
      if (!isCurrent(controller)) return;
      if (reviewLatest) {
        if (cause instanceof ApiError && cause.status === 412) {
          setProblem("conflict");
          setReviewed(null);
        } else if (
          !(cause instanceof ApiError) ||
          cause.status === 0 ||
          cause.status === 408 ||
          cause.status >= 500
        ) {
          setProblem("unconfirmed");
          setReviewed(null);
        } else toast.error(cause);
      }
      // Callers without review support retain their existing error presentation.
    } finally {
      pending.current = false;
      if (isCurrent(controller)) setSaving(false);
    }
  }

  async function review() {
    if (!reviewLatest || pending.current || reviewing || session !== getSessionVersion()) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setReviewed(null);
    setReviewing(true);
    try {
      const latest = await reviewLatest(controller.signal);
      if (!isCurrent(controller)) return;
      setDenied(false);
      setReviewed(latest);
    } catch (cause) {
      if (!isCurrent(controller)) return;
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) setDenied(true);
      else toast.error(cause);
    } finally {
      if (isCurrent(controller)) setReviewing(false);
    }
  }

  async function adopt() {
    if (!reviewed || pending.current || reviewing || session !== getSessionVersion()) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    pending.current = true;
    setSaving(true);
    try {
      await reviewed.adopt(controller.signal);
      if (isCurrent(controller)) onClose();
    } catch (cause) {
      if (isCurrent(controller)) toast.error(cause);
    } finally {
      pending.current = false;
      if (isCurrent(controller)) setSaving(false);
    }
  }

  if (session !== currentSession) return null;
  if (denied)
    return (
      <Modal open={open} onClose={close} title={uiText("Tags")}>
        <div role="alert" className="space-y-3">
          <p>{uiText("tags.reviewError")}</p>
          <Button variant="outline" loading={reviewing} onClick={() => void review()}>
            {uiText("Retry")}
          </Button>
        </div>
      </Modal>
    );

  return (
    <Modal
      open={open}
      onClose={close}
      title={uiText("Tags · {value1}", { value1: String(entityLabel) })}
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
            disabled={saving}
            onClick={() => void review()}
          >
            {uiText("library.reviewLatest")}
          </Button>
          {reviewed && (
            <section aria-label={uiText("library.latestVersion")} className="space-y-3">
              <h3 className="font-semibold">{uiText("library.latestVersion")}</h3>
              <p>{reviewed.tags.join(", ") || uiText("No tags assigned yet.")}</p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" disabled={saving} onClick={() => void adopt()}>
                  {uiText("library.useLatest")}
                </Button>
                <Button
                  size="sm"
                  loading={saving}
                  disabled={!reviewed.canEdit || reviewing}
                  onClick={() => void submit(reviewed)}
                >
                  {uiText("library.retryDraft")}
                </Button>
              </div>
            </section>
          )}
        </div>
      )}
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-on-surface-variant">{help}</p>
        <div className="flex flex-wrap gap-2" aria-label={uiText("Tags")}>
          {selected.length === 0 ? (
            <span className="text-sm text-on-surface-variant">
              {uiText("No tags assigned yet.")}
            </span>
          ) : (
            selected.map((tag) => (
              <button
                key={tag}
                disabled={saving || reviewed?.canEdit === false}
                type="button"
                onClick={() =>
                  setSelected((current) =>
                    current.filter((value) => normalized(value) !== normalized(tag)),
                  )
                }
                className="inline-flex max-w-full items-center gap-1 rounded-full border border-outline-variant bg-surface-container-low px-2.5 py-1 text-xs text-on-surface hover:border-primary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                title={uiText("Remove {value1}", { value1: String(tag) })}
              >
                <span className="truncate">{tag}</span>
                <X className="h-3 w-3 shrink-0" aria-hidden />
              </button>
            ))
          )}
        </div>
        <div className="space-y-2">
          <label htmlFor="entity-tag-input" className="text-sm font-medium text-on-surface">
            {uiText("Tags to add")}
          </label>
          <Input
            id="entity-tag-input"
            disabled={saving || reviewed?.canEdit === false}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add(input);
              }
            }}
            maxLength={64}
            placeholder={uiText("Search or create a tag")}
          />
          {suggestions.length > 0 && (
            <div
              className="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto"
              aria-label={uiText("Tags to add")}
            >
              {suggestions.slice(0, 30).map((tag) => (
                <Button
                  key={tag.id}
                  disabled={saving || reviewed?.canEdit === false}
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => add(tag.name)}
                >
                  {tag.name}
                </Button>
              ))}
            </div>
          )}
          {input.trim() &&
            !availableTags.some((tag) => normalized(tag.name) === normalized(input)) && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving || reviewed?.canEdit === false}
                onClick={() => add(input)}
              >
                {uiText("Create tag")}
              </Button>
            )}
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={close} disabled={saving}>
            {uiText("Cancel")}
          </Button>
          <Button
            type="button"
            onClick={() => void submit()}
            disabled={saving || reviewing || problem !== null}
          >
            {saving ? uiText("Saving…") : uiText("Save tags")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
