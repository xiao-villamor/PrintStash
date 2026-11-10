import { useId, useRef, useState, useSyncExternalStore } from "react";
import { X } from "lucide-react";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { userMessage } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Collection tags use the existing captured collection command, with inline editing. */
export function CollectionHeaderTags({
  tags: incoming,
  canEdit,
  onSave,
}: {
  tags: string[];
  canEdit: boolean;
  onSave: (tags: string[]) => Promise<string[]>;
}) {
  useUiLocale();
  const [tags, setTags] = useState(incoming);
  const [seen, setSeen] = useState(incoming);
  if (seen !== incoming) {
    setSeen(incoming);
    setTags(incoming);
  }
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const composing = useRef(false);
  const hintId = useId();
  const opener = useRef<HTMLButtonElement>(null);
  const active = session === currentSession;

  function cancel(restoreFocus = true) {
    if (pending.current) return;
    setEditing(false);
    setDraft("");
    setError(null);
    if (restoreFocus) requestAnimationFrame(() => opener.current?.focus());
  }
  async function save(next: string[], action: "add" | "remove") {
    if (pending.current || !canEdit || session !== getSessionVersion()) return;
    pending.current = true;
    setSaving(true);
    setError(null);
    try {
      const confirmed = await onSave(next);
      if (session === getSessionVersion()) {
        setTags(confirmed);
        if (action === "add") setDraft("");
        if (!editing) requestAnimationFrame(() => opener.current?.focus());
      }
    } catch (cause) {
      if (session === getSessionVersion()) setError(userMessage(cause));
    } finally {
      pending.current = false;
      if (session === getSessionVersion()) setSaving(false);
    }
  }
  function add() {
    if (pending.current || composing.current) return;
    const name = draft
      .trim()
      .toLowerCase()
      .replace(/^#+/, "")
      .replace(/\s+/g, "-")
      .replace(/[^\p{L}\p{N}_.-]/gu, "");
    if (!name) {
      setError(uiText("Type a tag name first."));
      return;
    }
    if (tags.some((tag) => tag.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      setError(uiText("#{value1} is already on this collection.", { value1: name }));
      return;
    }
    void save([...tags, name], "add");
  }
  if (!active) return null;
  return (
    <>
      <div className="collection-header-tags" aria-label={uiText("Tags")}>
        {tags.map((tag) => (
          <span key={tag} className="collection-header-tag">
            <span className="text-muted-foreground" aria-hidden>
              #
            </span>
            <span className="min-w-0 truncate" title={tag}>
              {tag}
            </span>
            {canEdit && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={saving}
                aria-label={uiText("Remove tag {value1}", { value1: tag })}
                onClick={() =>
                  void save(
                    tags.filter((value) => value !== tag),
                    "remove",
                  )
                }
              >
                <X aria-hidden className="h-3 w-3" />
              </Button>
            )}
          </span>
        ))}
        {canEdit &&
          (editing ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                add();
              }}
              className="collection-header-tag-input"
            >
              <span aria-hidden>#</span>
              <Input
                autoFocus
                aria-label={uiText("New tag")}
                aria-describedby={hintId}
                aria-invalid={Boolean(error)}
                placeholder={uiText("tag name")}
                maxLength={32}
                value={draft}
                readOnly={saving}
                aria-busy={saving}
                onCompositionStart={() => {
                  composing.current = true;
                }}
                onCompositionEnd={() => {
                  composing.current = false;
                }}
                onChange={(event) => {
                  setDraft(event.target.value);
                  setError(null);
                }}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing || composing.current) return;
                  if (event.key === "Enter" || event.key === ",") {
                    event.preventDefault();
                    add();
                  } else if (event.key === "Backspace" && !draft && tags.length) {
                    event.preventDefault();
                    void save(tags.slice(0, -1), "remove");
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    event.stopPropagation();
                    cancel();
                  }
                }}
                onBlur={() => {
                  if (!draft.trim()) cancel(false);
                }}
              />
            </form>
          ) : (
            <Button
              ref={opener}
              type="button"
              variant="outline"
              size="xs"
              disabled={saving}
              className="collection-add-tag"
              onClick={() => {
                setError(null);
                setEditing(true);
              }}
            >
              + {uiText("Add tag")}
            </Button>
          ))}
      </div>
      {(editing || error) && (
        <p
          id={hintId}
          role={error ? "alert" : "status"}
          className={`collection-tag-hint basis-full ${error ? "text-destructive" : "text-muted-foreground"}`}
        >
          {error ??
            uiText("Enter to add · Esc to cancel · Backspace on empty removes the last tag")}
        </p>
      )}
    </>
  );
}
