import type { CollectionRole } from "./models";

export interface MultipartBuildAttempt {
  id: number;
  job_id: number | null;
  historical_job_id: number;
  revision_id: number | null;
  planned_units: number;
  valid_units: number | null;
  suggested_valid_units: number;
  state: string;
  version: number;
}
export interface MultipartBuildPart {
  id: number;
  name: string;
  quantity: number;
  required_units: number;
  valid_units: number;
  missing_units: number;
  active_units: number;
  unreviewed_units: number;
  unreserved_units: number;
  selected_model_id: number | null;
  selected_choice_id: number | null;
  revision_id: number | null;
  queueable: boolean;
  choices: Array<{
    choice_id: number | null;
    model_id: number;
    name: string | null;
    available: boolean;
  }>;
  attempts: MultipartBuildAttempt[];
}
export interface MultipartBuild {
  id: number;
  name: string;
  composition_name: string;
  multipart_model_id: number | null;
  object_quantity: number;
  version: number;
  effective_role: CollectionRole | null;
  archived_at: string | null;
  created_at: string;
  completed: boolean;
  parts: MultipartBuildPart[];
}
