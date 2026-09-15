export interface SubjectCaption {
  state: "generated" | "edited" | "dismissed" | null;
  phase: "pending" | "running" | "ready" | "failed" | null;
  text: string;
  version_token: string | null;
  can_edit: boolean;
  can_generate: boolean;
  unavailable_reason: string | null;
  model: string | null;
  model_revision: string | null;
  recipe: string | null;
  edited_by: number | null;
  updated_at: string | null;
  error_code: string | null;
}
export interface CaptionPatch {
  action: "edit" | "dismiss" | "reset" | "generate";
  text?: string;
  version_token?: string;
}
