import { getJson, sendAction, sendJson } from "@/lib/api/request";
import type {
  InboxCapturedFieldName,
  InboxItem,
  InboxManifest,
  InboxManifestFile,
} from "@/types/inbox";

const capturedFieldNames = new Set<InboxCapturedFieldName>([
  "title",
  "description",
  "instructions",
  "creator_name",
  "creator_id",
  "creator_url",
  "license_code",
  "license_url",
  "license_text",
  "attribution_text",
  "published_at",
  "updated_at",
]);

interface InboxCapturedFieldWire {
  value: string;
  origin: string;
}
interface InboxManifestWire {
  schema_version?: number;
  kind?: string;
  source?: {
    provider?: string;
    canonical_url?: string;
    source_item_id?: string | null;
    source_revision?: string | null;
    adapter_version?: string;
    tags?: string[];
    fields?: Record<string, InboxCapturedFieldWire>;
  };
  files?: InboxManifestFile[] | object;
  selected_ids?: string[] | object;
  title?: string;
  entries?: InboxManifestFile[];
  members?: Array<{ id: string; title: string; page_url: string }>;
}
type InboxItemWire = Omit<InboxItem, "manifest"> & { manifest: InboxManifestWire };

export function parseInboxManifest(manifest: InboxManifestWire): InboxManifest | null {
  if (!manifest.kind) return null;
  if (manifest.schema_version === 2) {
    if (
      manifest.kind !== "model_files" ||
      !manifest.source?.provider ||
      !manifest.source.canonical_url ||
      !Array.isArray(manifest.source.tags) ||
      !Array.isArray(manifest.files) ||
      !Array.isArray(manifest.selected_ids) ||
      !manifest.source.fields ||
      Object.entries(manifest.source.fields).some(
        ([name, field]) =>
          // SAFETY: the Set is the closed backend captured-field allowlist.
          !capturedFieldNames.has(name as InboxCapturedFieldName) ||
          (field.origin !== "confirmed" && field.origin !== "inferred"),
      )
    ) {
      return null;
    }
    // SAFETY: Every V2 discriminator, required collection, and captured-field
    // origin is checked above against this closed wire contract.
    return manifest as InboxManifest;
  }
  if (manifest.schema_version !== undefined && manifest.schema_version !== 1) return null;
  if (!["direct", "archive", "model_files", "collection", "browser_file"].includes(manifest.kind))
    return null;
  // SAFETY: The legacy discriminator is checked against the complete V1 set.
  return manifest as InboxManifest;
}

function parsedInboxItem(item: InboxItemWire): InboxItem {
  const manifest = parseInboxManifest(item.manifest);
  if (manifest === null) throw new Error("Invalid inbox manifest response");
  return { ...item, manifest };
}

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
  return getJson<InboxItem[]>(`/api/v1/inbox?include_completed=${includeCompleted}`, {
    fresh: true,
  });
}

export function getPendingImport(id: number): Promise<InboxItem> {
  return getJson<InboxItemWire>(`/api/v1/inbox/${id}`, { fresh: true }).then(parsedInboxItem);
}

/**
 * Editable fields of a captured import, mirroring the backend's
 * `InboxItemUpdate` schema (partial update; the API forbids extra keys).
 */
export interface PendingImportUpdate {
  title?: string | null;
  collection_id?: number | null;
  tags?: string[];
  selected_ids?: string[];
}

export function updatePendingImport(id: number, payload: PendingImportUpdate): Promise<InboxItem> {
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
