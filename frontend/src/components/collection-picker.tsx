"use client";

import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { useCollectionSearch } from "@/lib/queries";
import type { CollectionNodeRead, CollectionRole } from "@/types";

export interface CollectionPickerProps {
  /** Offer only collections the reader holds at least this role on. */
  minRole: CollectionRole;
  /** The chosen collection's path; `""` when the "none" option is chosen; null for nothing yet. */
  selectedPath: string | null;
  /** Called with the chosen collection, or null for the "none" option. */
  onSelect: (collection: CollectionNodeRead | null) => void;
  /** Label of an extra first option meaning "no collection"; omitted when there is none. */
  noneLabel?: string;
  /** Collections that may not be chosen, e.g. a folder being moved and its subtree. */
  excludePaths?: string[];
  /** What to say when nothing matches. */
  emptyLabel: string;
}

/**
 * Pick a destination collection by searching for it.
 *
 * Pickers used to list every collection the reader could see, which on a large
 * library meant loading the whole tree to open a dialog (#295). This asks the
 * server for a page of matches instead, each labelled with its full name path.
 */
export function CollectionPicker({
  minRole,
  selectedPath,
  onSelect,
  noneLabel,
  excludePaths = [],
  emptyLabel,
}: CollectionPickerProps) {
  useUiLocale();
  const [query, setQuery] = useState("");
  const [searched, setSearched] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setSearched(query.trim()), 200);
    return () => clearTimeout(timer);
  }, [query]);
  const results = useCollectionSearch(searched, minRole);
  const options = (results.data?.pages.flatMap((page) => page.items) ?? []).filter(
    (collection) =>
      !excludePaths.some(
        (path) => collection.path === path || collection.path.startsWith(`${path}/`),
      ),
  );

  return (
    <Localized>
      <div>
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={uiText("Find destination...")}
          aria-label={uiText("Find destination")}
          className="mb-2"
        />
        <div
          role="listbox"
          aria-label={uiText("Collections")}
          className="max-h-72 overflow-y-auto rounded border border-border"
        >
          {noneLabel !== undefined && (
            <button
              type="button"
              role="option"
              aria-selected={selectedPath === ""}
              onClick={() => onSelect(null)}
              className={`w-full text-left px-3 py-2 font-mono text-xs transition-colors ${
                selectedPath === ""
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {noneLabel}
            </button>
          )}
          {options.map((collection) => (
            <button
              key={collection.id}
              type="button"
              role="option"
              aria-selected={selectedPath === collection.path}
              onClick={() => onSelect(collection)}
              className={`w-full text-left px-3 py-2 font-mono text-xs transition-colors ${
                selectedPath === collection.path
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {collection.display_path}{" "}
              <span className="opacity-50">({collection.model_count})</span>
            </button>
          ))}
          {results.hasNextPage && (
            <button
              type="button"
              disabled={results.isFetchingNextPage}
              onClick={() => void results.fetchNextPage()}
              className="w-full px-3 py-2 text-left font-mono text-3xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {uiText("Show more folders")}
            </button>
          )}
          {!results.isPending && options.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">{emptyLabel}</p>
          )}
        </div>
      </div>
    </Localized>
  );
}
