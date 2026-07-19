import { getJson, sendAction, sendJson } from "@/lib/api/request";
import type { InboxItem } from "@/types/inbox";

export function capturePendingImport(payload: {
  url: string;
  title?: string;
  source_kind?: "url" | "browser";
  collection_id?: number | null;
  tags?: string[];
}): Promise<InboxItem> {
  return sendJson<InboxItem>("/api/v1/inbox", "POST", payload);
}

export function listPendingImports(includeCompleted = true): Promise<InboxItem[]> {
  return getJson<InboxItem[]>(`/api/v1/inbox?include_completed=${includeCompleted}`, { fresh: true });
}

export function updatePendingImport(id: number, payload: Record<string, unknown>): Promise<InboxItem> {
  return sendJson<InboxItem>(`/api/v1/inbox/${id}`, "PATCH", payload);
}

export function importPendingImport(id: number, selectedIds: string[]): Promise<InboxItem> {
  return sendJson<InboxItem>(`/api/v1/inbox/${id}/import`, "POST", { selected_ids: selectedIds });
}

export function retryPendingImport(id: number): Promise<InboxItem> {
  return sendJson<InboxItem>(`/api/v1/inbox/${id}/retry`, "POST", {});
}

export function dismissPendingImport(id: number): Promise<void> {
  return sendAction(`/api/v1/inbox/${id}`, "DELETE");
}

export function batchPendingImports(payload: {
  item_ids: number[];
  action: "set_collection" | "add_tags" | "retry" | "import" | "dismiss";
  collection_id?: number | null;
  tags?: string[];
}): Promise<InboxItem[]> {
  return sendJson<InboxItem[]>("/api/v1/inbox/batch", "POST", payload);
}
