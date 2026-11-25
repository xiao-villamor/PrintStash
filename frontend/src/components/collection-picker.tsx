"use client";

import { useEffect, useState } from "react";

import { Check, Folder, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { useCollectionSearch } from "@/lib/queries";
import type { CollectionNodeRead, CollectionRole } from "@/types";

export interface CollectionPickerProps {
  /** Offer only collections the reader holds at least this role on. */
  minRole: CollectionRole;
  /** Focus the search when this picker opens inside a popover. */
  autoFocus?: boolean;
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
  autoFocus = false,
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
  const options = (
    results.isError ? [] : (results.data?.pages.flatMap((page) => page.items) ?? [])
  ).filter(
    (collection) =>
      !excludePaths.some(
        (path) => collection.path === path || collection.path.startsWith(`${path}/`),
      ),
  );

  const nameCounts = new Map<string, number>();
  for (const { name } of options) nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);

  return (
    <Localized>
      <div>
        <div className="collection-picker-search relative border-b border-border">
          <Search
            className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"
            aria-hidden
          />
          <Input
            autoFocus={autoFocus}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={uiText("Find destination...")}
            aria-label={uiText("Find destination")}
            className="h-9 border-0 pl-8 text-field shadow-none focus-visible:ring-inset focus-visible:ring-offset-0"
          />
        </div>
        <div
          role="listbox"
          aria-label={uiText("Collections")}
          className="collection-picker-options max-h-[280px] min-w-0 overflow-x-hidden overflow-y-auto"
        >
          {noneLabel !== undefined && (
            <button
              type="button"
              role="option"
              aria-selected={selectedPath === ""}
              onClick={() => onSelect(null)}
              className="field-option"
            >
              <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{noneLabel}</span>
              {selectedPath === "" && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
            </button>
          )}
          {options.map((collection) => (
            <button
              key={collection.id}
              type="button"
              role="option"
              aria-selected={selectedPath === collection.path}
              aria-label={`${collection.display_path} (${collection.model_count})`}
              title={collection.display_path}
              onClick={() => onSelect(collection)}
              className="field-option"
            >
              <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{collection.name}</span>
                {(nameCounts.get(collection.name) ?? 0) > 1 &&
                  collection.display_path !== collection.name && (
                    <span className="block truncate text-xs text-muted-foreground">
                      {collection.display_path.slice(0, -(collection.name.length + 1))}
                    </span>
                  )}
              </span>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                ({collection.model_count})
              </span>
              <span className="h-3.5 w-3.5 shrink-0" aria-hidden>
                {selectedPath === collection.path && <Check className="h-full w-full" />}
              </span>
            </button>
          ))}
          {results.isPending && (
            <p role="status" className="px-3 py-4 text-sm text-muted-foreground">
              {uiText("Loading...")}
            </p>
          )}
          {results.isSuccess && results.hasNextPage && (
            <button
              type="button"
              disabled={results.isFetchingNextPage}
              onClick={() => void results.fetchNextPage()}
              className="field-option"
            >
              {uiText("Show more folders")}
            </button>
          )}
          {results.isError && (
            <div role="alert" className="space-y-2 px-3 py-4 text-sm text-muted-foreground">
              <p>{uiText("Folders could not be loaded.")}</p>
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() => void results.refetch()}
              >
                {uiText("Retry")}
              </Button>
            </div>
          )}
          {results.isSuccess && options.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">{emptyLabel}</p>
          )}
        </div>
      </div>
    </Localized>
  );
}
