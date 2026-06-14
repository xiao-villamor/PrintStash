import { getJson, sendAction, sendJson } from "@/lib/api/request";
import {
  PublicModelRead,
  ShareLinkCreate,
  ShareLinkCreated,
  ShareLinkRead,
} from "@/types";

// Public (unauthenticated) — used by the /share/:token page.
export function getSharedModel(token: string): Promise<PublicModelRead> {
  return getJson<PublicModelRead>(`/api/v1/share/${token}`, { fresh: true });
}

export function sharedStlUrl(token: string, fileId: number): string {
  return `/api/v1/share/${token}/files/${fileId}/stl`;
}

export function sharedThumbnailUrl(token: string): string {
  return `/api/v1/share/${token}/thumbnail`;
}

export function sharedDownloadUrl(token: string, fileId: number): string {
  return `/api/v1/share/${token}/files/${fileId}/download`;
}

export function sharedGcodeUrl(token: string, fileId: number): string {
  return `/api/v1/share/${token}/files/${fileId}/gcode`;
}

// Authenticated management.
export function createModelShare(
  modelId: number,
  payload: ShareLinkCreate,
): Promise<ShareLinkCreated> {
  return sendJson<ShareLinkCreated>(
    `/api/v1/models/${modelId}/shares`,
    "POST",
    payload,
  );
}

export function listModelShares(modelId: number): Promise<ShareLinkRead[]> {
  return getJson<ShareLinkRead[]>(`/api/v1/models/${modelId}/shares`, {
    fresh: true,
  });
}

export function revokeShare(shareId: number): Promise<void> {
  return sendAction(`/api/v1/shares/${shareId}`, "DELETE");
}
