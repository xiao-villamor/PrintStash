import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { modelDetailOptions } from "@/features/library/model-detail";
import { useModelList, type ModelListFilters } from "@/lib/queries";
import { userMessage } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import type { FileRead, ModelListItem } from "@/types";

import {
  revisionLabel,
  type RevisionSelection,
  type useSavedRevision,
} from "@/features/printers/saved-revision";

export function SavedRevisionPicker({
  emptyLabel,
  pickerOpen,
  onOpenChange,
  onChoose,
  file,
  chosen,
}: {
  emptyLabel?: string;
  pickerOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onChoose: (selection: RevisionSelection) => void;
  file: FileRead | undefined;
  chosen: ReturnType<typeof useSavedRevision>["query"];
}) {
  const revisionId = useId();
  const [query, setQuery] = useState("");
  const [settledQuery, setSettledQuery] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledQuery(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const filters = useMemo<ModelListFilters>(
    () => ({ q: settledQuery.trim() || undefined, file_type: ["gcode"], direct: false }),
    [settledQuery],
  );
  const models = useModelList(filters, 24, "date-desc", pickerOpen);
  const rows = models.data?.pages.flatMap((page) => page.items) ?? [];
  const [browsedModel, setBrowsedModel] = useState<ModelListItem | null>(null);
  const browsed = useQuery({
    ...modelDetailOptions(browsedModel?.id ?? null),
    enabled: pickerOpen && browsedModel !== null,
  });
  const revisions = browsed.isError
    ? []
    : (browsed.data?.files.filter((file) => file.file_type === "gcode") ?? []);
  return (
    <div className="grid gap-2">
      <label className="text-[13px]" htmlFor={revisionId}>
        {uiText("Revision")}
      </label>
      <DropdownMenu
        open={pickerOpen}
        onOpenChange={(value) => {
          onOpenChange(value);
          if (!value) setBrowsedModel(null);
        }}
        portal
        align="start"
        role="dialog"
        aria-label={uiText("Revision")}
        className="w-full"
        contentClassName="w-[36rem] max-w-[calc(100vw-2rem)] overflow-hidden border border-border bg-popover text-popover-foreground shadow-lg"
        trigger={
          <Button
            id={revisionId}
            type="button"
            variant="outline"
            className="h-[38px] w-full justify-between border-input text-left text-[13px] font-normal"
            aria-haspopup="dialog"
            aria-expanded={pickerOpen}
            onClick={() => onOpenChange(!pickerOpen)}
          >
            <span className="min-w-0 truncate">
              {file && chosen.data
                ? `${chosen.data.name} · ${revisionLabel(file)}`
                : (emptyLabel ?? uiText("Select"))}
            </span>
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          </Button>
        }
      >
        {browsedModel ? (
          <div className="max-h-[60vh] overflow-y-auto p-3">
            <Button type="button" variant="ghost" size="xs" onClick={() => setBrowsedModel(null)}>
              <ChevronLeft className="h-3.5 w-3.5" />
              {uiText("Models")}
            </Button>
            <h3 className="my-3 break-words text-sm font-semibold">
              {browsed.data?.name ?? browsedModel.name}
            </h3>
            {browsed.isPending ? (
              <p role="status" className="py-4 text-xs text-muted-foreground">
                {uiText("Loading...")}
              </p>
            ) : browsed.isError ? (
              <div role="alert" className="space-y-2 text-sm">
                <p>{userMessage(browsed.error)}</p>
                <Button
                  type="button"
                  size="xs"
                  variant="outline"
                  onClick={() => void browsed.refetch()}
                >
                  {uiText("Retry")}
                </Button>
              </div>
            ) : revisions.length ? (
              <div className="divide-y divide-border">
                {revisions.map((revision) => (
                  <button
                    key={revision.id}
                    type="button"
                    className="block w-full px-2 py-3 text-left text-sm hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => {
                      onChoose({ modelId: browsedModel.id, fileId: revision.id });
                      setBrowsedModel(null);
                      onOpenChange(false);
                    }}
                  >
                    <span className="font-medium">
                      {revisionLabel(revision)}
                      {revision.is_recommended ? ` ★ ${uiText("Recommended")}` : ""}
                    </span>
                    <span className="mt-1 block break-words text-xs text-muted-foreground">
                      {revision.original_filename}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="py-4 text-sm text-muted-foreground">
                {uiText("No G-code revision yet")}
              </p>
            )}
          </div>
        ) : (
          <>
            <div className="border-b border-border p-3">
              <Input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                maxLength={512}
                aria-label={uiText("Search models")}
                placeholder={uiText("Search models")}
              />
            </div>
            <div className="max-h-64 overflow-y-auto p-1">
              {models.isError ? (
                <div role="alert" className="space-y-2 p-3 text-sm">
                  <p>{userMessage(models.error)}</p>
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    onClick={() => void models.refetch()}
                  >
                    {uiText("Retry")}
                  </Button>
                </div>
              ) : models.isPending ? (
                <p role="status" className="p-3 text-xs text-muted-foreground">
                  {uiText("Loading...")}
                </p>
              ) : rows.length ? (
                rows.map((model) => (
                  <button
                    key={model.id}
                    type="button"
                    disabled={models.isPlaceholderData}
                    onClick={() => setBrowsedModel(model)}
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                  >
                    <span className="min-w-0 break-words">{model.name}</span>
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 -rotate-90 text-muted-foreground" />
                  </button>
                ))
              ) : (
                <p className="p-3 text-sm text-muted-foreground">{uiText("No models found")}</p>
              )}
            </div>
            {models.hasNextPage && (
              <div className="border-t border-border p-2">
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  className="w-full"
                  loading={models.isFetchingNextPage}
                  onClick={() => void models.fetchNextPage()}
                >
                  {uiText("Load more")}
                </Button>
              </div>
            )}
          </>
        )}
      </DropdownMenu>
      {chosen.isError && (
        <p role="alert" className="text-xs text-destructive">
          {userMessage(chosen.error)}
        </p>
      )}
    </div>
  );
}
