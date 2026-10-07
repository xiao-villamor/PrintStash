import { getJson, getPublicJson, sendAction, sendJson } from "@/lib/api/request";
import { PublicModelRead, ShareLinkCreate, ShareLinkCreated, ShareLinkRead } from "@/types";

// Public (unauthenticated) — used by the /share/:token page.
export function getSharedModel(
  token: string,
  options?: { signal?: AbortSignal },
): Promise<PublicModelRead> {
  return getPublicJson<PublicModelRead>(`/api/v1/share/${encodeURIComponent(token)}`, options);
}

export function sharedStlUrl(token: string, fileId: number): string {
  return `/api/v1/share/${encodeURIComponent(token)}/files/${fileId}/stl`;
}

export function sharedThumbnailUrl(token: string): string {
  return `/api/v1/share/${encodeURIComponent(token)}/thumbnail`;
}

export function sharedDownloadUrl(token: string, fileId: number): string {
  return `/api/v1/share/${encodeURIComponent(token)}/files/${fileId}/download`;
}

export function sharedGcodeUrl(token: string, fileId: number): string {
  return `/api/v1/share/${encodeURIComponent(token)}/files/${fileId}/toolpath`;
}

// Authenticated management.
export function createModelShare(
  modelId: number,
  payload: ShareLinkCreate,
): Promise<ShareLinkCreated> {
  return sendJson<ShareLinkCreated>(`/api/v1/models/${modelId}/shares`, "POST", payload);
}

export function listModelShares(modelId: number): Promise<ShareLinkRead[]> {
  return getJson<ShareLinkRead[]>(`/api/v1/models/${modelId}/shares`, {});
}

export function revokeShare(shareId: number): Promise<void> {
  return sendAction(`/api/v1/shares/${shareId}`, "DELETE");
}
