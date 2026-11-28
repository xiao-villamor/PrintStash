import { useId, useRef, useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";

export type CollectionCreateOutcome =
  | { status: "saved" }
  | { status: "reported" }
  | { status: "invalid"; message: string };

export function NewCollectionCard({
  maxLength,
  onCreate,
}: {
  maxLength: number;
  /** Resolves true when saved, false when the failure was already reported, or the
   * message to show beside the field. */
  onCreate: (name: string) => Promise<CollectionCreateOutcome>;
}) {
  useUiLocale();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const restoreFocus = useRef(false);
  const errorId = useId();
  const name = draft.trim();
  const tooLong = [...name].length > maxLength;

  function close() {
    restoreFocus.current = true;
    setProblem(null);
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
        if (saving) return;
        if (!name) {
          setProblem(uiText("Enter a collection name."));
          return;
        }
        if (tooLong) return;
        setSaving(true);
        try {
          const saved = await onCreate(name);
          if (saved.status === "invalid") setProblem(saved.message);
          else if (saved.status === "saved") close();
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
          aria-invalid={tooLong || problem !== null || undefined}
          aria-describedby={tooLong || problem ? errorId : undefined}
          className="library-collection-name min-w-0 flex-1"
          value={draft}
          readOnly={saving}
          onChange={(event) => {
            setDraft(event.target.value);
            setProblem(null);
          }}
        />
        <Button
          type="submit"
          variant="ghost"
          size="icon-sm"
          aria-label={uiText("Create collection")}
          title={uiText("Create collection")}
          disabled={tooLong || saving}
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
      {(tooLong || problem) && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {tooLong ? uiText("collection.nameTooLong", { limit: maxLength }) : problem}
        </p>
      )}
    </form>
  );
}
