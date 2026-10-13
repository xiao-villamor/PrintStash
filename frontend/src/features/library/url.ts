import type { LibraryViewMode, ModelSort } from "@/types";

export type { LibraryViewMode } from "@/types";

const SORTS: readonly ModelSort[] = [
  "relevance",
  "date-desc",
  "date-asc",
  "name-asc",
  "name-desc",
  "success-desc",
  "printed-desc",
  "duration-asc",
  "filament-asc",
  "cost-asc",
];

interface LibraryPreferences {
  view: string | null;
  sort: string | null;
}

export interface LibraryLocation {
  view: LibraryViewMode;
  sort: ModelSort;
  href: string;
  section: "models" | "docs";
}

/**
 * The URL wins over initial display preferences. Retired/unrecognized modes
 * explicitly become Everything; malformed sorts become Newest. Canonicalizing
 * both values captures a reproducible navigation entry before preferences change.
 */
export function readLibraryLocation(
  source: URLSearchParams,
  preferences: LibraryPreferences = { view: null, sort: null },
  preferredSection: "models" | "docs" = "models",
): LibraryLocation {
  const params = new URLSearchParams(source);
  const requestedView =
    source.get("type") ?? (source.get("v") === "multipart" ? "multipart" : preferences.view);
  const view = requestedView === "multipart" ? "multipart" : "all";
  const requestedSort = source.get("sort") ?? preferences.sort;
  const sort = SORTS.find((candidate) => candidate === requestedSort) ?? "date-desc";
  const requestedSection = source.get("v");
  const section =
    requestedSection === "docs"
      ? "docs"
      : requestedSection !== null || source.has("type")
        ? "models"
        : preferredSection;
  if (section === "docs") params.set("v", "docs");
  else if (requestedSection !== "models") params.delete("v");
  params.set("type", view);
  params.set("sort", sort);
  return { view, sort, section, href: `/?${params}` };
}
