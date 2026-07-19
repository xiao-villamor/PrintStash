export type InboxItemState = "captured" | "resolving" | "review" | "importing" | "completed" | "failed" | "dismissed";

export interface InboxItem {
  id: number;
  owner_user_id: number;
  source_kind: "url" | "browser" | "upload" | "external";
  source_url: string | null;
  display_title: string | null;
  source_hostname: string | null;
  state: InboxItemState;
  manifest: {
    kind?: "direct" | "archive" | "model_files" | "collection";
    title?: string;
    selected_ids?: string[];
    entries?: Array<{ id: string; name: string; size: number; file_type: string }>;
    files?: Array<{ id: string; name: string; size?: number; file_type: string }>;
    members?: Array<{ id: string; title: string; page_url: string }>;
  };
  target_collection_id: number | null;
  requested_tags: string[];
  background_job_id: string | null;
  resulting_model_id: number | null;
  error_code: string | null;
  retryable: boolean;
  attempt_count: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}
