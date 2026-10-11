import type {
  CollectionNodeRead,
  ListModelsParams,
  OutlinerItemRead,
  OutlinerModelRead,
} from "./models";

export type OutlinerView = "organized" | "all" | "multipart" | "components";
export type OutlinerFilters = Omit<
  ListModelsParams,
  "collection" | "direct" | "q" | "limit" | "offset"
>;
export type OutlinerEntry =
  | (OutlinerModelRead & { kind: "model" })
  | (OutlinerModelRead & { kind: "multipart" });
export type OutlinerMatch = OutlinerEntry | (OutlinerItemRead & { kind: "collection" });
export interface OutlinerCollection extends CollectionNodeRead {
  direct_entry_count: number;
  subtree_entry_count: number;
  visible_child_count: number;
}
export interface OutlinerCollectionPage {
  items: OutlinerCollection[];
  next_cursor: string | null;
  parent_direct_entry_count: number;
  revealed: OutlinerCollection | null;
}
export interface OutlinerEntryPage {
  items: OutlinerEntry[];
  next_cursor: string | null;
}
export interface OutlinerSearchPage {
  items: OutlinerMatch[];
  next_cursor: string | null;
}
export interface OutlinerParams extends OutlinerFilters {
  view: OutlinerView;
  cursor?: string;
  parent_id?: number;
  collection_id?: number;
  reveal_id?: number;
  q?: string;
}

export interface OutlinerRestoreParams extends OutlinerFilters {
  view: OutlinerView;
  expanded_paths: string[];
  selected_path: string | null;
}
export interface OutlinerRestoreRead {
  collections: { parent_id: number | null; page: OutlinerCollectionPage }[];
  entries: { collection_id: number | null; page: OutlinerEntryPage }[];
}
