import { useRef, useState } from "react";
import {
  ArrowLeft,
  BadgeCheck,
  Calendar,
  Check,
  ChevronRight,
  Copy,
  Cpu,
  FileBox,
  GitBranch,
  HardDrive,
  History,
  Layers,
  Palette,
  Search,
  Star,
  Tags,
  X,
  type LucideIcon,
} from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { LibraryFilterChoice } from "@/components/library-filter-choice";
import { PrintHistoryFields } from "@/components/print-history-fields";
import {
  structuredLibraryFilterLabel,
  type StructuredLibraryFilterKey,
} from "@/features/library/filters";
import { filterValueText } from "@/lib/filter-labels";
import { historyKeys } from "@/lib/search-filters";
import { useI18n } from "@/lib/i18n";
import { FILTER_SECTIONS, QUICK_FILTERS } from "@/features/library/filter-sections";
import type { ModelFacetsRead, PrinterRead, SavedViewFilters, TagRead } from "@/types";

export type LibraryFilterField =
  | StructuredLibraryFilterKey
  | "tag"
  | "printer_id"
  | "printer_presence"
  | "favorites"
  | "uploaded_after"
  | "uploaded_before"
  | (typeof historyKeys)[number];
type Category =
  | keyof ModelFacetsRead
  | "has_similar_candidates"
  | "tags"
  | "printer"
  | "favorites"
  | "uploaded"
  | "history";
type Choice = {
  value: string;
  label: string;
  checked: boolean;
  count?: number;
  select: (checked: boolean) => void;
};
const ICONS = {
  material_type: Palette,
  file_type: FileBox,
  revision_status: GitBranch,
  printed: Check,
  slicer_name: Layers,
  printer_model: Cpu,
  print_outcome: BadgeCheck,
  storage: HardDrive,
  has_similar_candidates: Copy,
  tags: Tags,
  printer: Printer3D,
  favorites: Star,
  uploaded: Calendar,
  history: History,
} as const satisfies Record<Category, LucideIcon>;
const facetsOrder: (keyof ModelFacetsRead)[] = [
  "material_type",
  "file_type",
  "revision_status",
  "printed",
  "slicer_name",
  "printer_model",
  "print_outcome",
  "storage",
];

/** One searchable picker; URL-owned selections update results without a draft or Apply step. */
export function LibraryFilterPicker({
  filters,
  facets,
  tags,
  printers,
  canViewPrinters,
  loading,
  error,
  onRetry,
  onChange,
  onReset,
  onClose,
}: {
  filters: SavedViewFilters;
  facets?: ModelFacetsRead;
  tags: TagRead[];
  printers: PrinterRead[];
  canViewPrinters: boolean;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  onChange: (key: LibraryFilterField, values: string[]) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const needle = query.trim().toLocaleLowerCase();
  const matches = (label: string) => label.toLocaleLowerCase().includes(needle);
  const groups: { id: Category; label: string; choices: Choice[] }[] = facetsOrder.map((key) => {
    const selected =
      key === "printed"
        ? filters.printed == null
          ? []
          : [filters.printed ? "yes" : "no"]
        : (filters[key] ?? []);
    const values = facets?.[key] ?? [];
    const options = [
      ...values,
      ...selected
        .filter((value) => !values.some((item) => item.value === value))
        .map((value) => ({ value, count: undefined })),
    ];
    return {
      id: key,
      label: structuredLibraryFilterLabel(key),
      choices: options.map((item) => ({
        ...item,
        label:
          key === "printed"
            ? t(item.value === "yes" ? "Printed" : "Not printed")
            : filterValueText(key, item.value),
        checked: selected.includes(item.value),
        select: (checked) =>
          onChange(
            key,
            key === "printed"
              ? checked
                ? [item.value]
                : []
              : checked
                ? [...selected, item.value]
                : selected.filter((value) => value !== item.value),
          ),
      })),
    };
  });
  groups.push({
    id: "tags",
    label: t("Tags"),
    choices: [
      ...tags.map((tag) => ({
        value: tag.slug,
        label: tag.name,
        count: tag.model_count + (tag.multipart_model_count ?? 0),
      })),
      ...filters.tag
        .filter((slug) => !tags.some((tag) => tag.slug === slug))
        .map((slug) => ({ value: slug, label: slug, count: undefined })),
    ].map((tag) => ({
      ...tag,
      checked: filters.tag.includes(tag.value),
      select: (checked) =>
        onChange(
          "tag",
          checked
            ? [...filters.tag, tag.value]
            : filters.tag.filter((value) => value !== tag.value),
        ),
    })),
  });
  if (canViewPrinters)
    groups.push({
      id: "printer",
      label: t("Printer"),
      choices: [
        {
          value: "any",
          label: t("On a printer"),
          checked: filters.printer_presence === "any",
          select: (checked) => onChange("printer_presence", checked ? ["any"] : []),
        },
        {
          value: "none",
          label: t("Vault only"),
          checked: filters.printer_presence === "none",
          select: (checked) => onChange("printer_presence", checked ? ["none"] : []),
        },
        ...printers.map((printer) => ({
          value: String(printer.id),
          label: printer.name,
          checked: filters.printer_id === printer.id,
          select: (checked: boolean) => onChange("printer_id", checked ? [String(printer.id)] : []),
        })),
      ],
    });
  groups.push(
    {
      id: "favorites",
      label: t("Favorites"),
      choices: [
        {
          value: "true",
          label: t("Favorites"),
          checked: filters.favorites,
          select: (checked) => onChange("favorites", checked ? ["true"] : []),
        },
      ],
    },
    {
      id: "has_similar_candidates",
      label: structuredLibraryFilterLabel("has_similar_candidates"),
      choices: ([true, false] as const).map((value) => ({
        value: value ? "yes" : "no",
        label: t(value ? "Yes" : "No"),
        checked: filters.has_similar_candidates === value,
        select: (checked) =>
          onChange("has_similar_candidates", checked ? [value ? "yes" : "no"] : []),
      })),
    },
    { id: "uploaded", label: t("Uploaded"), choices: [] },
    { id: "history", label: t("Print history"), choices: [] },
  );
  function activeCount(group: (typeof groups)[number]) {
    if (group.id === "uploaded")
      return Number(!!filters.uploaded_after) + Number(!!filters.uploaded_before);
    if (group.id === "printer")
      return Number(filters.printer_id != null || filters.printer_presence != null);
    if (group.id === "history") return historyKeys.filter((key) => filters[key] != null).length;
    return group.choices.filter((choice) => choice.checked).length;
  }
  const count = groups.reduce((sum, group) => sum + activeCount(group), 0);
  const selectedGroup = groups.find((group) => group.id === category);
  const visible = groups.flatMap((group) => {
    if (category && category !== group.id) return [];
    const choices = group.choices.filter((choice) => matches(group.label) || matches(choice.label));
    return !needle || matches(group.label) || choices.length ? [{ ...group, choices }] : [];
  });
  function clearGroup(id: Category) {
    if (id === "tags") onChange("tag", []);
    else if (id === "printer") {
      onChange("printer_id", []);
      onChange("printer_presence", []);
    } else if (id === "uploaded") {
      onChange("uploaded_after", []);
      onChange("uploaded_before", []);
    } else if (id === "history") {
      for (const key of historyKeys) if (filters[key] != null) onChange(key, []);
    } else onChange(id, []);
  }
  /** What a category currently narrows to, in the words its values use. */
  function summary(group: (typeof groups)[number]) {
    if (group.id === "uploaded") {
      const { uploaded_after: after, uploaded_before: before } = filters;
      if (after && before) return `${after} – ${before}`;
      if (after) return `${t("After")} ${after}`;
      if (before) return `${t("Before")} ${before}`;
      return null;
    }
    if (group.id === "history") {
      const active = activeCount(group);
      return active ? t("library.filters.conditions", { count: active }) : null;
    }
    const labels = group.choices.filter((choice) => choice.checked).map((choice) => choice.label);
    return labels.length ? labels.join(", ") : null;
  }
  function categoryRow(group: (typeof groups)[number]) {
    const Icon = ICONS[group.id];
    const active = activeCount(group);
    const selected = summary(group);
    return (
      <button
        key={group.id}
        type="button"
        onClick={() => choose(group.id)}
        aria-label={selected ? `${group.label}: ${selected}` : group.label}
        data-active={active > 0 || undefined}
        className="group/filter flex min-h-9 w-full items-center gap-2.5 rounded px-2 py-1.5 text-left text-sm transition-colors duration-press hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-sm ${active ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground"}`}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </span>
        <span className="shrink-0">{group.label}</span>
        <span className="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground">
          {selected}
        </span>
        {active > 1 && (
          <span
            aria-hidden
            className="shrink-0 rounded-sm bg-accent px-1 text-2xs tabular-nums text-accent-foreground"
          >
            {active}
          </span>
        )}
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      </button>
    );
  }
  function quickRow(group: (typeof groups)[number]) {
    const Icon = ICONS[group.id];
    const label = (
      <>
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm bg-muted text-muted-foreground">
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate">{group.label}</span>
      </>
    );
    if (group.id === "favorites") {
      const choice = group.choices[0]!;
      return (
        <div key={group.id} className="flex min-h-9 items-center gap-2.5 px-2 py-1 text-sm">
          {label}
          <Switch
            checked={choice.checked}
            onChange={choice.select}
            label={group.label}
            stateLabels={null}
          />
        </div>
      );
    }
    return (
      <div key={group.id} className="flex min-h-9 items-center gap-2.5 px-2 py-1 text-sm">
        {label}
        <div
          role="group"
          aria-label={group.label}
          className="flex shrink-0 divide-x divide-border border border-border"
        >
          {[null, ...group.choices].map((choice) => {
            const pressed = choice ? choice.checked : !group.choices.some((item) => item.checked);
            return (
              <button
                key={choice?.value ?? "any"}
                type="button"
                aria-pressed={pressed}
                onClick={() => {
                  if (choice) choice.select(true);
                  else clearGroup(group.id);
                }}
                className={`h-6 px-2 text-xs transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${pressed ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-popover-hover hover:text-foreground"}`}
              >
                {choice?.label ?? t("library.filters.any")}
              </button>
            );
          })}
        </div>
      </div>
    );
  }
  function choose(id: Category | null) {
    setCategory(id);
    setQuery("");
    input.current?.focus();
  }
  return (
    <div id="library-filter-panel" className="flex min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2.5">
        {category && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("Back")}
            onClick={() => choose(null)}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
          </Button>
        )}
        <span className="flex-1 text-sm font-medium">{selectedGroup?.label ?? t("Filters")}</span>
        {selectedGroup && activeCount(selectedGroup) > 0 && (
          <Button variant="ghost" size="xs" onClick={() => clearGroup(selectedGroup.id)}>
            {t("Clear")}
          </Button>
        )}
        <Button variant="ghost" size="icon-sm" aria-label={t("Close")} onClick={onClose}>
          <X className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      <div className="relative mx-3 my-2 shrink-0">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          ref={input}
          autoFocus
          type="search"
          aria-label={t("library.filters.search")}
          placeholder={t("library.filters.search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="h-9 pl-8 text-sm"
        />
      </div>
      <div className="min-h-0 max-h-[45dvh] overflow-y-auto px-1.5 pb-2">
        {loading && (
          <p role="status" className="px-3 py-2 text-xs text-muted-foreground">
            {t("Loading filter values…")}
          </p>
        )}
        {error && (
          <div role="alert" className="px-3 py-2 text-xs">
            <p>{t("Filter values could not be loaded.")}</p>
            <Button variant="ghost" size="xs" onClick={onRetry}>
              {t("Retry")}
            </Button>
          </div>
        )}
        {visible.length === 0 && (
          <p role="status" className="px-3 py-5 text-sm text-muted-foreground">
            {t("library.filters.noMatches")}
          </p>
        )}
        {!category && !needle && (
          <>
            <section
              aria-label={t("library.filters.sectionQuick")}
              className="mb-1 border-b border-border px-1 pb-2 pt-1"
            >
              <h3 className="px-2 pb-1 pt-1.5 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                {t("library.filters.sectionQuick")}
              </h3>
              {QUICK_FILTERS.map((id) => {
                const group = groups.find((item) => item.id === id);
                return group ? quickRow(group) : null;
              })}
            </section>
            {FILTER_SECTIONS.map((section) => (
              <section key={section.label} aria-label={t(section.label)} className="px-1 py-1">
                <h3 className="px-2 pb-1 pt-1.5 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                  {t(section.label)}
                </h3>
                {section.ids.map((id) => {
                  const group = groups.find((item) => item.id === id);
                  return group ? categoryRow(group) : null;
                })}
              </section>
            ))}
          </>
        )}
        {(category !== null || needle.length > 0) &&
          visible.map((group) => {
            return (
              <section key={group.id} aria-label={group.label} className="px-1 py-1">
                {!category && (
                  <h3 className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                    {group.label}
                  </h3>
                )}
                {group.id === "uploaded" ? (
                  <div className="grid grid-cols-2 gap-2 px-2 py-2">
                    {(["uploaded_after", "uploaded_before"] as const).map((key) => (
                      <label key={key} className="space-y-1 text-xs text-muted-foreground">
                        {t(key === "uploaded_after" ? "After" : "Before")}
                        <Input
                          type="date"
                          value={filters[key] ?? ""}
                          onChange={(event) =>
                            onChange(key, event.target.value ? [event.target.value] : [])
                          }
                        />
                      </label>
                    ))}
                  </div>
                ) : group.id === "history" ? (
                  <div className="px-2">
                    <PrintHistoryFields
                      compact
                      datePicker
                      value={filters}
                      onChange={(key, value) => onChange(key, value ? [value] : [])}
                    />
                  </div>
                ) : (
                  <>
                    {group.choices.map((choice) => (
                      <LibraryFilterChoice
                        key={choice.value}
                        label={choice.label}
                        count={choice.count}
                        checked={choice.checked}
                        onChange={choice.select}
                      />
                    ))}
                    {group.choices.length === 0 && !loading && !error && (
                      <p role="status" className="px-2 py-3 text-xs text-muted-foreground">
                        {t("library.filters.noValues")}
                      </p>
                    )}
                  </>
                )}
              </section>
            );
          })}
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-border px-3 py-2">
        <Check className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="flex-1 text-xs text-muted-foreground">{t("library.filters.instant")}</span>
        <Button
          variant="ghost"
          size="xs"
          disabled={count === 0}
          onClick={() => {
            onReset();
            input.current?.focus();
          }}
        >
          {t("Reset filters")}
        </Button>
      </div>
    </div>
  );
}
