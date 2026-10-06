import { useMemo, useState } from "react";
import { X } from "lucide-react";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { TagRead } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase();
}

export function EntityTagsEditor({
  entityLabel,
  tags,
  availableTags,
  help,
  onSave,
  open,
  onClose,
}: {
  entityLabel: string;
  tags: string[];
  availableTags: TagRead[];
  help: string;
  onSave: (tags: string[]) => Promise<void>;
  open: boolean;
  onClose: () => void;
}) {
  useUiLocale();
  const [selected, setSelected] = useState<string[]>(tags);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);

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

  async function submit() {
    setSaving(true);
    try {
      await onSave(selected);
      onClose();
    } catch {
      // The caller owns user-facing error reporting (normally a toast). Keep
      // the dialog open so the selection can be retried without data loss.
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !saving && onClose()}
      title={uiText("Tags · {value1}", { value1: String(entityLabel) })}
    >
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
              <Button type="button" variant="outline" size="sm" onClick={() => add(input)}>
                {uiText("Create tag")}
              </Button>
            )}
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => onClose()} disabled={saving}>
            {uiText("Cancel")}
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={saving}>
            {saving ? uiText("Saving…") : uiText("Save tags")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
