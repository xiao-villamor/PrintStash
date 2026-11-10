import { useCallback, useLayoutEffect, useRef } from "react";
import { Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { uiText } from "@/lib/locale";

/** The collection prototype replaces the heading in place while renaming. */
export function CollectionTitleEditor({
  title,
  canEdit,
  open,
  value,
  busy,
  onOpen,
  onCancel,
  onValueChange,
  onSave,
}: {
  title: string;
  canEdit: boolean;
  open: boolean;
  value: string;
  busy: boolean;
  onOpen: () => void;
  onCancel: () => void;
  onValueChange: (value: string) => void;
  onSave: () => Promise<void>;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const cancelled = useRef(false);
  const edit = useRef(0);
  const pending = useRef<number | null>(null);
  const restoreFocus = useRef(false);
  const focusInput = useCallback((input: HTMLInputElement | null) => {
    if (!input) return;
    edit.current += 1;
    cancelled.current = false;
    input.focus();
  }, []);
  useLayoutEffect(() => {
    if (!open && restoreFocus.current) {
      restoreFocus.current = false;
      trigger.current?.focus();
    }
  }, [open]);

  async function commit() {
    const generation = edit.current;
    if (cancelled.current || pending.current === generation || busy) return;
    if (!value.trim() || value.trim() === title) {
      onCancel();
      return;
    }
    pending.current = generation;
    try {
      await onSave();
    } finally {
      if (pending.current === generation) pending.current = null;
    }
  }

  return (
    <div className="library-collection-title flex min-w-0 items-center gap-1.5">
      <h1
        className={open ? "sr-only" : "min-w-0 break-words text-foreground"}
        onDoubleClick={onOpen}
      >
        {title}
      </h1>
      {open && (
        <Input
          ref={focusInput}
          aria-label={uiText("inbox.collectionName")}
          className="library-collection-name"
          value={value}
          maxLength={100}
          readOnly={busy}
          aria-busy={busy}
          onChange={(event) => onValueChange(event.target.value)}
          onBlur={() => {
            restoreFocus.current = false;
            void commit();
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter") {
              event.preventDefault();
              restoreFocus.current = true;
              void commit();
            } else if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              if (busy || pending.current === edit.current) return;
              cancelled.current = true;
              restoreFocus.current = true;
              onCancel();
            }
          }}
        />
      )}
      {canEdit && (
        <Button
          ref={trigger}
          type="button"
          variant="ghost"
          size="icon-sm"
          hidden={open}
          className="shrink-0 text-muted-foreground"
          aria-label={uiText("Rename {value1}", { value1: title })}
          title={uiText("Rename (F2)")}
          onClick={onOpen}
        >
          <Pencil className="h-4 w-4" aria-hidden />
        </Button>
      )}
    </div>
  );
}
