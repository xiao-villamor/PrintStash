export type ProvenanceOrigin = "confirmed" | "inferred" | "user";

export type ProvenanceFieldName =
  | "title"
  | "description"
  | "instructions"
  | "creator_name"
  | "creator_id"
  | "creator_url"
  | "license_code"
  | "license_url"
  | "license_text"
  | "attribution_text"
  | "published_at"
  | "updated_at";

export interface ProvenanceFieldRead {
  field_name: ProvenanceFieldName;
  captured_value: string;
  captured_origin: Exclude<ProvenanceOrigin, "user">;
  user_value: string | null;
  user_override_set: boolean;
  effective_value: string;
  effective_origin: ProvenanceOrigin;
  captured_at: string | null;
  user_updated_at: string | null;
}

export interface ProvenanceCaptureSummaryRead {
  id: number;
  snapshot_sha256: string;
  adapter_version: string;
  source_revision: string | null;
  captured_at: string;
  checked_at: string;
}

export interface ProvenanceSourceRead {
  id: number;
  provider: string;
  source_item_id: string | null;
  canonical_url: string;
  source_revision: string | null;
  tags?: string[];
  first_captured_at: string;
  last_checked_at: string;
  fields: ProvenanceFieldRead[];
  captures: ProvenanceCaptureSummaryRead[];
}

export interface ModelProvenanceRead {
  sources: ProvenanceSourceRead[];
}

/** Metadata for a private representative cover attached to a source. */
export interface ModelSourceCoverRead {
  id: number;
  provenance_source_id: number;
  content_type: "image/webp";
  size_bytes: number;
  updated_at: string;
}

export type ModelProvenancePatch = {
  overrides: Partial<{ [Name in ProvenanceFieldName]: string }>;
  clear_overrides: ProvenanceFieldName[];
};
