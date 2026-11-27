import { useId, useState, type ReactNode } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { FILTER_SECTIONS, QUICK_FILTERS } from "@/features/library/filter-sections";
import { structuredLibraryFilterLabel } from "@/features/library/filters";
import { FILTER_SELECT } from "@/lib/filter-input";
import { PrintHistoryFields } from "@/components/print-history-fields";
import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { uiText } from "@/lib/locale";
import { useI18n } from "@/lib/i18n";
import { filterValueText } from "@/lib/filter-labels";
import {
  historyFilters,
  readSearchFilters,
  searchSorts,
  writeSearchFilters,
} from "@/lib/search-filters";
import type { MessageKey } from "@/lib/locale";
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
  has_similar_candidates: "aiSearch.filter.has_similar_candidates",
} satisfies Record<string, MessageKey>;
function filterLabel(key: string): MessageKey {
  return Object.entries(labels).find(([name]) => name === key)?.[1] ?? "aiSearch.activeFilters";
}
function choiceValues(key: string): string[] {
  switch (key) {
    case "favorites":
      return ["true"];
    case "printed":
    case "has_similar_candidates":
      return ["yes", "no"];
    case "revision_status":
      return ["known_good", "needs_test", "failed", "archived"];
    case "printer_presence":
      return ["any", "none"];
    case "print_outcome":
      return ["completed", "failed", "cancelled"];
    case "storage":
      return ["vault", "external"];
    default:
      return [];
  }
}
function choiceLabel(key: string, value: string): string {
  if (key === "printer_presence") return uiText(value === "any" ? "On a printer" : "Vault only");
  if (key === "printed") return uiText(value === "yes" ? "Printed" : "Not printed");
  if (key === "favorites" || key === "has_similar_candidates")
    return uiText(value === "no" ? "No" : "Yes");
  return filterValueText(key, value);
}
export function SearchFilterControls({
  filters,
  q,
  sort,
  showQuery = true,
  actions,
  onChange,
}: {
  filters: SavedViewFilters;
  q: string;
  sort: ModelSort;
  showQuery?: boolean;
  actions: ReactNode;
  onChange: (filters: SavedViewFilters, q: string, sort: ModelSort) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState(q);
  const filtersFormId = useId();
  const [addOpen, setAddOpen] = useState(false);
  const [addKey, setAddKey] = useState("tag");
  const [addValue, setAddValue] = useState("");
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
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <form
          key={q}
          className="flex flex-wrap items-center gap-2"
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
          {showQuery && <Button type="submit">{t("aiSearch.applySearch")}</Button>}
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() => {
              setAddKey("tag");
              setAddValue("");
              setDraft(params);
              setAddOpen(true);
            }}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
            {uiText("Filters")}
          </Button>
          <label className="flex items-center gap-2 text-xs">
            {t("aiSearch.sort")}
            <select
              aria-label={t("aiSearch.sort")}
              className={FILTER_SELECT}
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
          </label>
        </form>
        {actions}
      </div>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-2" aria-label={t("aiSearch.activeFilters")}>
          {chips.map(([key, value], index) => {
            const label =
              key === "print_outcome" && value === "completed"
                ? t("aiSearch.successfulPrint")
                : key === "print_duration_min_s" || key === "print_duration_max_s"
                  ? `${t(filterLabel(key))} ${filterValueText(key, value)}`
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
      )}
      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title={uiText("Filters")}
        description={uiText("Search filters apply when you select Apply filters.")}
        footer={
          <>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              {uiText("Cancel")}
            </Button>
            <Button
              type="submit"
              form={filtersFormId}
              disabled={!addValue.trim() && draft.toString() === params.toString()}
            >
              {t("aiSearch.applyFilters")}
            </Button>
          </>
        }
      >
        <form
          id={filtersFormId}
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            const next = new URLSearchParams(draft);
            const value = addValue.trim();
            if (!value && draft.toString() === params.toString()) return;
            if (value) {
              if (
                [
                  "c",
                  "printer_presence",
                  "printed",
                  "favorites",
                  "has_similar_candidates",
                  "uploaded_after",
                  "uploaded_before",
                ].includes(addKey)
              ) {
                if (addKey === "printer_presence") next.delete("printer_id");
                next.set(addKey, value);
              } else if (!next.getAll(addKey).includes(value)) next.append(addKey, value);
            }
            onChange(readSearchFilters(next), q, sort);
            setAddOpen(false);
          }}
        >
          <label className="block space-y-1 text-sm">
            {uiText("Filter")}
            <select
              className={inputClasses}
              value={addKey}
              onChange={(event) => {
                setAddKey(event.target.value);
                setAddValue("");
              }}
            >
              <optgroup label={t("library.filters.sectionQuick")}>
                {QUICK_FILTERS.map((key) => (
                  <option key={key} value={key}>
                    {key === "favorites" ? uiText("Favorites") : structuredLibraryFilterLabel(key)}
                  </option>
                ))}
              </optgroup>
              {FILTER_SECTIONS.map(({ label, ids }) => (
                <optgroup key={label} label={t(label)}>
                  {ids.flatMap((key) => {
                    switch (key) {
                      case "tags":
                        return [
                          <option key="tag" value="tag">
                            {uiText("Tags")}
                          </option>,
                          <option key="c" value="c">
                            {uiText("Collection")}
                          </option>,
                        ];
                      case "uploaded":
                        return ["uploaded_after", "uploaded_before"].map((value) => (
                          <option key={value} value={value}>
                            {t(filterLabel(value))}
                          </option>
                        ));
                      case "printer":
                        return (
                          <option key="printer_presence" value="printer_presence">
                            {uiText("Printer")}
                          </option>
                        );
                      case "history":
                        return (
                          <option key={key} value={key}>
                            {uiText("Print history")}
                          </option>
                        );
                      default:
                        return (
                          <option key={key} value={key}>
                            {structuredLibraryFilterLabel(key)}
                          </option>
                        );
                    }
                  })}
                </optgroup>
              ))}
            </select>
          </label>
          {addKey === "history" ? (
            <>
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
            </>
          ) : (
            <label className="block space-y-1 text-sm">
              {uiText("Filter value")}
              {choiceValues(addKey).length ? (
                <select
                  className="h-10 w-full rounded-md border border-input bg-field-surface px-3"
                  value={addValue}
                  onChange={(event) => setAddValue(event.target.value)}
                >
                  <option value="">—</option>
                  {choiceValues(addKey).map((value) => (
                    <option key={value} value={value}>
                      {choiceLabel(addKey, value)}
                    </option>
                  ))}
                </select>
              ) : (
                <Input
                  type={
                    addKey === "uploaded_after" || addKey === "uploaded_before" ? "date" : "text"
                  }
                  value={addValue}
                  maxLength={512}
                  onChange={(event) => setAddValue(event.target.value)}
                />
              )}
            </label>
          )}
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
            {editing?.key.includes("duration") && ` (${t("aiSearch.seconds")})`}
            {editing?.key === "print_outcome" || editing?.key === "printed" ? (
              <select
                className={inputClasses}
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
