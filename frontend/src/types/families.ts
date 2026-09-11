import type {
  ArtifactFileType,
  CollectionRole,
  FileRead,
  ListModelPageParams,
  ModelListItem,
  ModelSort,
} from "./models";

export type MemberRole = "identical" | "rescaled" | "mirrored" | "repaired" | "print_variant";
export type VariantRole = "canonical" | MemberRole;
export type FamilyBrowseMode = "models" | "families_collapsed";

export interface ModelFamilySummary {
  id: number;
  name: string;
  slug: string;
  version: number;
  member_id: number;
  role: VariantRole;
  member_count: number;
  canonical_model_id: number | null;
  effective_role: CollectionRole;
}

export interface FamilyRead {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  collection_id: number | null;
  collection: string | null;
  version: number;
  canonical_model_id: number | null;
  canonical_member_id: number | null;
  cover_model_id: number | null;
  cover_thumbnail_url: string | null;
  cover_image_uploaded: boolean;
  cover_image_url: string | null;
  member_count: number;
  total_visible_members: number;
  matching_visible_members: number;
  tags: string[];
  starred: boolean;
  effective_role: CollectionRole;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface FamilyMemberRead {
  id: number;
  model_id: number;
  role: VariantRole;
  transformation_note: string | null;
  scale_factor: number | null;
  mirrored: boolean;
  mirror_verified: boolean;
  relative_review_required: boolean;
  joined_via: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface FamilyMemberItem extends FamilyMemberRead {
  model: ModelListItem;
  preview_file: FileRead | null;
  formats: string[];
  source_file_count: number;
  gcode_revision_count: number;
  known_good_count: number;
  latest_print_outcome: string | null;
  units: "mm" | "unknown";
}

export interface FamilyPage<T> {
  items: T[];
  total: number;
  next_cursor: string | null;
}
export type FamilyBrowseCard =
  | { kind: "family"; family: FamilyRead }
  | { kind: "model"; model: ModelListItem };
export type FamilyBrowseParams = ListModelPageParams;
export interface FamilyListParams {
  q?: string;
  collection_id?: number;
  favorites?: boolean;
  tag?: string[];
  trashed?: boolean;
  sort?: ModelSort;
  cursor?: string;
  limit?: number;
}
export interface FamilyMemberParams {
  q?: string;
  role?: VariantRole;
  file_type?: ArtifactFileType;
  known_good?: boolean;
  has_revisions?: boolean;
  source?: "vault" | "external";
  sort?: "order" | "scale-asc" | "scale-desc" | "date-asc" | "date-desc" | "success-desc";
  cursor?: string;
  limit?: number;
}
export interface FamilyMemberInput {
  model_id: number;
  role?: MemberRole;
  transformation_note?: string | null;
  scale_factor?: number | null;
  mirrored?: boolean;
  mirror_verified?: boolean;
  sort_order?: number;
}
export interface FamilyCreate {
  name: string;
  canonical_model_id: number;
  members: FamilyMemberInput[];
  description?: string;
  collection_id?: number;
}
export interface FamilyUpdate {
  version: number;
  name?: string;
  description?: string | null;
  collection_id?: number | null;
  tags?: string[];
  cover_model_id?: number | null;
  cover_image_url?: string | null;
}

export const MEMBER_ROLES: MemberRole[] = [
  "identical",
  "rescaled",
  "mirrored",
  "repaired",
  "print_variant",
];
