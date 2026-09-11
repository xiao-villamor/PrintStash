import type { FamilyMemberItem, FamilyRead, ModelFamilySummary } from "@/types/families";
import { aModelListItem, FROZEN_NOW } from "./factories";

export function aFamily(overrides: Partial<FamilyRead> = {}): FamilyRead {
  return {
    id: 7,
    name: "Benchy variations",
    slug: "benchy-variations",
    description: null,
    collection_id: null,
    collection: null,
    version: 3,
    canonical_model_id: 1,
    canonical_member_id: 11,
    cover_model_id: null,
    cover_thumbnail_url: null,
    cover_image_uploaded: false,
    cover_image_url: null,
    member_count: 2,
    total_visible_members: 2,
    matching_visible_members: 2,
    tags: [],
    starred: false,
    effective_role: "admin",
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    deleted_at: null,
    ...overrides,
  };
}

export function aFamilySummary(overrides: Partial<ModelFamilySummary> = {}): ModelFamilySummary {
  return {
    id: 7,
    name: "Benchy variations",
    slug: "benchy-variations",
    version: 3,
    member_id: 11,
    role: "canonical",
    member_count: 2,
    canonical_model_id: 1,
    effective_role: "admin",
    ...overrides,
  };
}

export function aFamilyMember(overrides: Partial<FamilyMemberItem> = {}): FamilyMemberItem {
  return {
    id: 11,
    model_id: 1,
    role: "canonical",
    transformation_note: null,
    scale_factor: 1,
    mirrored: false,
    mirror_verified: true,
    relative_review_required: false,
    joined_via: "manual",
    sort_order: 0,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    model: aModelListItem({ name: "Benchy", family: aFamilySummary() }),
    preview_file: null,
    formats: ["stl"],
    source_file_count: 1,
    gcode_revision_count: 0,
    known_good_count: 0,
    latest_print_outcome: null,
    units: "unknown",
    ...overrides,
  };
}
