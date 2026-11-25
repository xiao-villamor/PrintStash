import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";

export interface FindableModel {
  id: number;
  name: string;
}

/**
 * Find in the open collection, as in an editor: it walks the matches one at a
 * time instead of filtering the grid, so the collection keeps its place.
 */
export function LibraryFindBar({
  models,
  onReveal,
  onClose,
}: {
  models: readonly FindableModel[];
  /** Bring one match into view; null once nothing is matched. */
  onReveal: (id: number | null) => void;
  onClose: () => void;
}) {
  useUiLocale();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState(0);
  const needle = query.trim().toLocaleLowerCase();
  const matches = useMemo(
    () =>
      needle === ""
        ? []
        : models.filter((model) => model.name.toLocaleLowerCase().includes(needle)),
    [models, needle],
  );
  const position = matches.length === 0 ? 0 : Math.min(current, matches.length - 1);
  const target = matches[position]?.id ?? null;

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  useEffect(() => {
    onReveal(target);
  }, [onReveal, target]);
  useEffect(() => () => onReveal(null), [onReveal]);

  function step(direction: 1 | -1) {
    if (matches.length === 0) return;
    setCurrent((position + direction + matches.length) % matches.length);
  }

  const status =
    needle === ""
      ? ""
      : matches.length === 0
        ? uiText("No results")
        : uiText("{value1} of {value2}", { value1: position + 1, value2: matches.length });

  return (
    <div
      role="search"
      aria-label={uiText("Find in collection")}
      className="animate-panel-in fixed right-6 top-16 z-30 flex items-center gap-1 rounded-lg border border-border bg-card p-1.5 shadow-lg"
    >
      <input
        ref={input}
        type="text"
        value={query}
        aria-label={uiText("Find in collection")}
        placeholder={uiText("Find")}
        onChange={(event) => {
          setQuery(event.target.value);
          setCurrent(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            step(event.shiftKey ? -1 : 1);
          } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            onClose();
          }
        }}
        className="h-8 w-56 rounded-md border border-input bg-background px-2.5 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <span
        role="status"
        aria-live="polite"
        className="min-w-16 px-1.5 text-center text-xs tabular-nums text-muted-foreground"
      >
        {status}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={uiText("Previous match")}
        disabled={matches.length === 0}
        onClick={() => step(-1)}
      >
        <ChevronUp className="size-4" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={uiText("Next match")}
        disabled={matches.length === 0}
        onClick={() => step(1)}
      >
        <ChevronDown className="size-4" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={uiText("Close find")}
        onClick={onClose}
      >
        <X className="size-4" aria-hidden />
      </Button>
    </div>
  );
}
