import { getJson, sendAction, sendForm, sendJson, type GetJsonOptions } from "@/lib/api/request";
import type { DocumentListItem, DocumentRead } from "@/types";

export function listDocuments(
  collection: string | null,
  options?: GetJsonOptions,
): Promise<DocumentListItem[]> {
  const qs = collection ? `?collection=${encodeURIComponent(collection)}` : "";
  return getJson<DocumentListItem[]>(`/api/v1/documents${qs}`, options);
}

export function getDocument(id: number): Promise<DocumentRead> {
  return getJson<DocumentRead>(`/api/v1/documents/${id}`, { fresh: true });
}

export function createDocument(payload: {
  name: string;
  collection_id: number | null;
  body?: string;
  multipart_model_id?: number | null;
}): Promise<DocumentRead> {
  return sendJson<DocumentRead>("/api/v1/documents", "POST", payload);
}

export function uploadDocument(
  file: File,
  collectionId: number | null,
  name?: string,
  multipartModelId?: number,
): Promise<DocumentRead> {
  const form = new FormData();
  form.append("file", file);
  if (collectionId != null) form.append("collection_id", String(collectionId));
  if (name) form.append("name", name);
  if (multipartModelId != null) form.append("multipart_model_id", String(multipartModelId));
  return sendForm<DocumentRead>("/api/v1/documents/upload", form);
}

export function updateDocument(
  id: number,
  payload: { name?: string; body?: string },
): Promise<DocumentRead> {
  return sendJson<DocumentRead>(`/api/v1/documents/${id}`, "PUT", payload);
}

export function deleteDocument(id: number): Promise<void> {
  return sendAction(`/api/v1/documents/${id}`, "DELETE");
}

export function uploadDocumentImage(id: number, file: File): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  return sendForm<{ url: string }>(`/api/v1/documents/${id}/images`, form);
}
