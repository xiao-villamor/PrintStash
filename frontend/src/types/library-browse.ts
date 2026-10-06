import type { LibraryViewMode, ListModelsParams, ModelListItem, ModelSort } from "./models";
import type { MultipartModelListItem } from "./multipart-models";

export type LibraryBrowseEntry =
  | { kind: "model"; model: ModelListItem }
  | { kind: "multipart"; multipart: MultipartModelListItem };

export interface LibraryAuthority {
  browse_revision: string;
  authorization_revision: string;
}

export interface LibraryBrowsePage extends LibraryAuthority {
  items: LibraryBrowseEntry[];
  next_cursor: string | null;
  /** Combined card count, not the number of Models. */
  total: number;
}

export interface LibraryBrowseParams extends Omit<ListModelsParams, "offset" | "limit"> {
  view: LibraryViewMode;
  sort: ModelSort;
  limit: number;
}
