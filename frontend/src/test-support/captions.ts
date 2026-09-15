import type { SubjectCaption } from "@/types/captions";

export function aCaption(overrides: Partial<SubjectCaption> = {}): SubjectCaption {
  return {
    state: "generated",
    phase: "ready",
    text: "A mounting bracket",
    version_token: "a".repeat(32),
    can_edit: true,
    can_generate: true,
    unavailable_reason: null,
    model: "test-vlm",
    model_revision: "test",
    recipe: "caption-thumbnail-v1",
    edited_by: null,
    updated_at: "2026-09-12T00:00:00Z",
    error_code: null,
    ...overrides,
  };
}
