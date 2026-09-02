import type { CollectionRole } from "./models";
import type { DocumentListItem } from "./documents";

export interface MultipartModelReference {
  id: number;
  /** The composition row id. Candidates do not have one until selected. */
  choice_id?: number;
  name: string | null;
  slug: string | null;
  thumbnail_url: string | null;
  source_file_count: number;
  gcode_revision_count: number;
  available: boolean;
  /** Set only for choices imported from the pre-0.13 part-options tables. */
  legacy_label?: string | null;
  /** The exact legacy file selected by an imported choice, when available. */
  source_file_id?: number | null;
}

export interface MultipartModelListItem {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  collection: string | null;
  collection_id: number | null;
  part_count: number;
  model_count: number;
  guide_count: number;
  cover_model_id: number | null;
  cover_thumbnail_url: string | null;
  effective_role: CollectionRole | null;
  updated_at: string;
}

export interface MultipartPartRead {
  id: number;
  name: string;
  sort_order: number;
  models: MultipartModelReference[];
}

export interface MultipartModelRead extends MultipartModelListItem {
  created_at: string;
  parts: MultipartPartRead[];
  guides: DocumentListItem[];
}

export interface MultipartModelCreate {
  name: string;
  description?: string | null;
  collection_id?: number | null;
}

export interface MultipartModelUpdate {
  name?: string;
  description?: string | null;
}

export interface MultipartPartsWrite {
  name: string;
  description: string | null;
  collection_id: number | null;
  cover_model_id: number | null;
  parts: Array<{
    name: string;
    choices: Array<{ model_id: number; choice_id?: number }>;
  }>;
}

export type MultipartModelCandidate = MultipartModelReference;
