import type { CollectionRole } from "./models";

export type DocumentKind = "markdown" | "pdf" | "other";

export interface DocumentListItem {
  id: number;
  name: string;
  kind: DocumentKind;
  collection: string | null;
  collection_id: number | null;
  multipart_model_id: number | null;
  filename: string | null;
  effective_role: CollectionRole | null;
  updated_at: string;
}

export interface DocumentRead extends DocumentListItem {
  body: string | null;
}
