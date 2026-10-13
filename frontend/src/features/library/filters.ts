import { historyFilters, historyKeys } from "@/lib/search-filters";
import type { ListModelsParams, SavedViewFilters } from "@/types";
import { readLibraryLocation, type LibraryLocation } from "./url";

export const structuredLibraryFilterKeys = [
  "file_type",
  "material_type",
  "slicer_name",
  "printer_model",
  "revision_status",
  "print_outcome",
  "storage",
  "printed",
  "has_similar_candidates",
] as const;
export type StructuredLibraryFilterKey = (typeof structuredLibraryFilterKeys)[number];

const arrayKeys = [
  "file_type",
  "material_type",
  "slicer_name",
  "printer_model",
  "revision_status",
  "print_outcome",
  "storage",
] as const;

function accepted<T extends string>(
  params: URLSearchParams,
  key: string,
  choices: readonly T[],
): T[] {
  return params.getAll(key).flatMap((value) => {
    const match = choices.find((choice) => choice === value);
    return match === undefined ? [] : [match];
  });
}

function printerIdentity(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) return null;
  const identity = Number(value);
  return Number.isSafeInteger(identity) && identity > 0 ? identity : null;
}

function booleanFilter(value: string | null): boolean | null {
  return value === "yes" ? true : value === "no" ? false : null;
}

/** Normalize only controlled values; unrelated URL parameters keep their order. */
function replaceValues(params: URLSearchParams, key: string, values: string[]) {
  const current = params.getAll(key);
  if (current.length === values.length && current.every((value, index) => value === values[index]))
    return;
  params.delete(key);
  values.forEach((value) => params.append(key, value));
}

/** One effective Library state for requests, controls and newly saved views. */
export function readLibraryFilters(
  source: URLSearchParams,
  location: Pick<LibraryLocation, "view" | "sort">,
  canViewPrinters: boolean,
) {
  const params = new URLSearchParams(source);
  const printerId = canViewPrinters ? printerIdentity(source.get("printer_id")) : null;
  const rawPresence = source.get("printer_presence");
  const printerPresence =
    canViewPrinters && printerId === null && (rawPresence === "any" || rawPresence === "none")
      ? rawPresence
      : null;
  const printed = booleanFilter(source.get("printed"));
  const similar = booleanFilter(source.get("has_similar_candidates"));
  const structured = {
    file_type: accepted(source, "file_type", [
      "stl",
      "3mf",
      "gcode",
      "obj",
      "step",
      "dxf",
    ] as const),
    material_type: source.getAll("material_type"),
    slicer_name: source.getAll("slicer_name"),
    printer_model: source.getAll("printer_model"),
    revision_status: accepted(source, "revision_status", [
      "known_good",
      "needs_test",
      "failed",
      "archived",
    ] as const),
    print_outcome: accepted(source, "print_outcome", [
      "queued",
      "uploading",
      "started",
      "printing",
      "paused",
      "completed",
      "cancelled",
      "failed",
    ] as const),
    storage: accepted(source, "storage", ["vault", "external"] as const),
    printed: printed === null ? [] : [printed ? "yes" : "no"],
    has_similar_candidates: similar === null ? [] : [similar ? "yes" : "no"],
  } satisfies Record<StructuredLibraryFilterKey, string[]>;
  for (const key of structuredLibraryFilterKeys) replaceValues(params, key, structured[key]);
  replaceValues(params, "printer_id", printerId === null ? [] : [String(printerId)]);
  replaceValues(params, "printer_presence", printerPresence === null ? [] : [printerPresence]);
  const query = source.get("q")?.trim() || null;
  const filters = {
    ...historyFilters(source),
    library_view: location.view,
    sort: location.sort,
    collection: source.get("c") || null,
    direct: !query,
    q: query,
    tag: source.getAll("tag"),
    favorites: source.get("favorites") === "true",
    printer_id: printerId,
    printer_presence: printerPresence,
    ...structured,
    printed,
    has_similar_candidates: similar,
    uploaded_after: source.get("uploaded_after") || null,
    uploaded_before: source.get("uploaded_before") || null,
  } satisfies SavedViewFilters;
  const baseFilters: Omit<ListModelsParams, "limit" | "offset"> = {
    ...historyFilters(source),
    tag: filters.tag.length ? filters.tag : undefined,
    favorites: filters.favorites || undefined,
    printer_id: printerId ?? undefined,
    printer_presence: printerPresence ?? undefined,
    ...structured,
    printed: printed ?? undefined,
    has_similar_candidates: similar ?? undefined,
    uploaded_after: filters.uploaded_after ?? undefined,
    uploaded_before: filters.uploaded_before ?? undefined,
  };
  return { filters, structured, baseFilters, params };
}

/** Library bookmarks use type + explicit date-desc; Search has another contract. */
export function writeLibraryFilters(
  filters: SavedViewFilters,
  canViewPrinters: boolean,
): URLSearchParams {
  const params = new URLSearchParams();
  params.set("type", filters.library_view);
  params.set("sort", filters.sort ?? "date-desc");
  if (filters.collection) params.set("c", filters.collection);
  if (filters.q) params.set("q", filters.q);
  filters.tag.forEach((tag) => params.append("tag", tag));
  if (filters.printer_id != null) params.set("printer_id", String(filters.printer_id));
  if (filters.printer_presence) params.set("printer_presence", filters.printer_presence);
  if (filters.favorites) params.set("favorites", "true");
  for (const key of arrayKeys) for (const value of filters[key] ?? []) params.append(key, value);
  if (filters.has_similar_candidates != null)
    params.set("has_similar_candidates", filters.has_similar_candidates ? "yes" : "no");
  if (filters.printed != null) params.set("printed", filters.printed ? "yes" : "no");
  if (filters.uploaded_after) params.set("uploaded_after", filters.uploaded_after);
  if (filters.uploaded_before) params.set("uploaded_before", filters.uploaded_before);
  for (const key of historyKeys) if (filters[key] != null) params.set(key, String(filters[key]));
  return readLibraryFilters(params, readLibraryLocation(params), canViewPrinters).params;
}

/** A new copy uses current capability without modifying the source saved DTO. */
export function effectiveLibraryView(
  filters: SavedViewFilters,
  canViewPrinters: boolean,
): SavedViewFilters {
  const params = writeLibraryFilters(filters, canViewPrinters);
  return readLibraryFilters(params, readLibraryLocation(params), canViewPrinters).filters;
}

export function sameLibraryFilters(
  left: SavedViewFilters,
  right: SavedViewFilters,
  canViewPrinters: boolean,
): boolean {
  function signature(filters: SavedViewFilters) {
    const effective = effectiveLibraryView(filters, canViewPrinters);
    return JSON.stringify({ ...effective, tag: [...effective.tag].sort() });
  }
  return signature(left) === signature(right);
}
