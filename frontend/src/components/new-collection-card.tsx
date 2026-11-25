import { useId, useRef, useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";

export function NewCollectionCard({
  maxLength,
  onCreate,
}: {
  maxLength: number;
  onCreate: (name: string) => Promise<boolean>;
}) {
  useUiLocale();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const restoreFocus = useRef(false);
  const errorId = useId();
  const name = draft.trim();
  const tooLong = [...name].length > maxLength;

  function close() {
    restoreFocus.current = true;
    setDraft("");
    setEditing(false);
  }

  if (!editing) {
    return (
      <Button
        ref={(node) => {
          if (!node || !restoreFocus.current) return;
          restoreFocus.current = false;
          node.focus();
        }}
        variant="outline"
        className="h-full min-h-20 w-full justify-start gap-3 rounded-lg border-dashed px-3 text-muted-foreground hover:border-ring hover:text-foreground"
        onClick={() => setEditing(true)}
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-md bg-muted">
          <Plus className="size-5" aria-hidden />
        </span>
        {uiText("New collection")}
      </Button>
    );
  }

  return (
    <form
      aria-busy={saving}
      className="flex h-full min-h-20 min-w-0 flex-col justify-center gap-1.5 px-3 py-2"
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.nativeEvent.isComposing || saving) return;
        event.preventDefault();
        event.stopPropagation();
        close();
      }}
      onSubmit={async (event) => {
        event.preventDefault();
        if (!name || tooLong || saving) return;
        setSaving(true);
        try {
          if (await onCreate(name)) close();
        } finally {
          setSaving(false);
        }
      }}
    >
      <div className="flex items-center gap-1.5">
        <Input
          autoFocus
          aria-label={uiText("collection.newName")}
          placeholder={uiText("collection.newName")}
          aria-invalid={tooLong || undefined}
          aria-describedby={tooLong ? errorId : undefined}
          className="library-collection-name min-w-0 flex-1"
          value={draft}
          readOnly={saving}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          type="submit"
          variant="ghost"
          size="icon-sm"
          aria-label={uiText("Create collection")}
          title={uiText("Create collection")}
          disabled={!name || tooLong || saving}
        >
          <Check className="size-4" aria-hidden />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={uiText("Cancel")}
          title={uiText("Cancel")}
          disabled={saving}
          onClick={close}
        >
          <X className="size-4" aria-hidden />
        </Button>
      </div>
      {tooLong && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {uiText("collection.nameTooLong", { limit: maxLength })}
        </p>
      )}
    </form>
  );
}
