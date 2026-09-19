import { useState } from "react";
import { X } from "lucide-react";
import { PrintHistoryFields } from "@/components/print-history-fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useI18n } from "@/lib/i18n";
import { filterValueText } from "@/lib/filter-labels";
import {
  historyFilters,
  readSearchFilters,
  searchSorts,
  writeSearchFilters,
} from "@/lib/search-filters";
import type { MessageKey } from "@/locales/catalogs";
import type { ModelSort, SavedViewFilters } from "@/types";

const labels = {
  c: "aiSearch.filter.collection",
  direct: "aiSearch.filter.direct",
  tag: "aiSearch.filter.tag",
  favorites: "aiSearch.filter.favorites",
  printer_id: "aiSearch.filter.printer_id",
  printer_presence: "aiSearch.filter.printer_presence",
  printed: "aiSearch.filter.printed",
  print_outcome: "aiSearch.filter.print_outcome",
  material_type: "aiSearch.filter.material_type",
  file_type: "aiSearch.filter.file_type",
  revision_status: "aiSearch.filter.revision_status",
  storage: "aiSearch.filter.storage",
  slicer_name: "aiSearch.filter.slicer_name",
  printer_model: "aiSearch.filter.printer_model",
  uploaded_after: "aiSearch.filter.uploaded_after",
  uploaded_before: "aiSearch.filter.uploaded_before",
  printed_after: "aiSearch.filter.printed_after",
  printed_before: "aiSearch.filter.printed_before",
  print_duration_min_s: "aiSearch.filter.print_duration_min_s",
  print_duration_max_s: "aiSearch.filter.print_duration_max_s",
  family_id: "aiSearch.filter.family_id",
  family_role: "aiSearch.filter.family_role",
  in_family: "aiSearch.filter.in_family",
  has_similar_candidates: "aiSearch.filter.has_similar_candidates",
} satisfies Record<string, MessageKey>;
function filterLabel(key: string): MessageKey {
  return Object.entries(labels).find(([name]) => name === key)?.[1] ?? "aiSearch.activeFilters";
}
export function SearchFilterControls({
  filters,
  q,
  sort,
  showQuery = true,
  onChange,
}: {
  filters: SavedViewFilters;
  q: string;
  sort: ModelSort;
  showQuery?: boolean;
  onChange: (filters: SavedViewFilters, q: string, sort: ModelSort) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState(q);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [draft, setDraft] = useState(new URLSearchParams());
  const [editing, setEditing] = useState<{ key: string; value: string; index: number } | null>(
    null,
  );
  const [editValue, setEditValue] = useState("");
  const params = writeSearchFilters(filters);
  const chips = [...params.entries()];
  function updateChip(index: number, value?: string) {
    const next = new URLSearchParams();
    chips.forEach(([key, old], position) => {
      if (position !== index) next.append(key, old);
      else if (value) next.append(key, value);
    });
    onChange(readSearchFilters(next), q, sort);
  }
  return (
    <div className="mb-4 space-y-3">
      <form
        key={q}
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          onChange(filters, query, sort);
        }}
      >
        {showQuery && (
          <Input
            className="min-w-0 flex-1 basis-full sm:basis-auto"
            aria-label={t("aiSearch.residualQuery")}
            defaultValue={q}
            maxLength={512}
            onChange={(event) => setQuery(event.target.value)}
          />
        )}
        {showQuery && (
          <Button type="submit" variant="outline">
            {t("aiSearch.applySearch")}
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setDraft(params);
            setHistoryOpen(true);
          }}
        >
          {t("aiSearch.printHistory")}
        </Button>
        <select
          aria-label={t("aiSearch.sort")}
          className="rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={sort}
          onChange={(event) =>
            onChange(
              filters,
              q,
              searchSorts.find((value) => value === event.target.value) ?? "relevance",
            )
          }
        >
          {searchSorts.map((value) => (
            <option key={value} value={value}>
              {t(`aiSearch.sort.${value}`)}
            </option>
          ))}
        </select>
      </form>
      <div className="flex flex-wrap gap-2" aria-label={t("aiSearch.activeFilters")}>
        {chips.map(([key, value], index) => {
          const label =
            key === "print_outcome" && value === "completed"
              ? t("aiSearch.successfulPrint")
              : `${t(filterLabel(key))}: ${filterValueText(key, value)}`;
          return (
            <span
              key={`${key}:${value}`}
              className="inline-flex max-w-full items-center rounded-md bg-accent text-accent-foreground"
            >
              <Button
                size="sm"
                variant="ghost"
                className="min-w-0 whitespace-normal break-words text-left"
                onClick={() => {
                  setEditing({ key, value, index });
                  setEditValue(value);
                }}
                aria-label={t("aiSearch.editFilter", { filter: label })}
              >
                {label}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("aiSearch.removeFilter", { filter: label })}
                onClick={() => updateChip(index)}
              >
                <X className="h-3 w-3" />
              </Button>
            </span>
          );
        })}
      </div>
      <Modal
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        title={t("aiSearch.printHistory")}
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            onChange(readSearchFilters(draft), q, sort);
            setHistoryOpen(false);
          }}
        >
          <p className="text-sm text-muted-foreground">{t("aiSearch.historyHelp")}</p>
          <PrintHistoryFields
            value={historyFilters(draft)}
            onChange={(key, value) =>
              setDraft((current) => {
                const next = new URLSearchParams(current);
                if (value) next.set(key, value);
                else next.delete(key);
                return next;
              })
            }
          />
          <Button type="submit">{t("aiSearch.applyFilters")}</Button>
        </form>
      </Modal>
      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={t("aiSearch.editFilterTitle")}
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (editing) updateChip(editing.index, editValue);
            setEditing(null);
          }}
        >
          <label className="block space-y-1 text-sm">
            {t(editing ? filterLabel(editing.key) : "aiSearch.activeFilters")}
            {editing?.key === "print_outcome" || editing?.key === "printed" ? (
              <select
                className="block w-full rounded-md border border-input bg-background p-2"
                value={editValue}
                onChange={(event) => setEditValue(event.target.value)}
              >
                {(editing.key === "printed"
                  ? ["yes", "no"]
                  : ["completed", "failed", "cancelled"]
                ).map((value) => (
                  <option key={value} value={value}>
                    {filterValueText(editing.key, value)}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                value={editValue}
                maxLength={512}
                onChange={(event) => setEditValue(event.target.value)}
              />
            )}
          </label>
          <Button type="submit">{t("aiSearch.applyFilters")}</Button>
        </form>
      </Modal>
    </div>
  );
}
