import { authHeaders, getUrl, handleResponse, jsonHeaders } from "@/lib/api/request";

export type ArtifactUploadMode = "api_chunks" | "native_parts" | "simple";
export type ArtifactUploadState =
  | "created"
  | "uploading"
  | "verifying"
  | "ingesting"
  | "completed"
  | "failed"
  | "aborted"
  | "expired";

export interface ArtifactUploadPart {
  index: number;
  offset: number;
  size_bytes: number;
  sha256: string;
}

export interface ArtifactUploadStatus {
  id: string;
  purpose: string;
  target_role: string;
  target_id: string | null;
  filename: string;
  media_type: string;
  size_bytes: number;
  state: ArtifactUploadState;
  mode: ArtifactUploadMode;
  received_bytes: number;
  verified_size: number | null;
  verified_sha256: string | null;
  job_id: string | null;
  retryable: boolean;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
  parts: ArtifactUploadPart[];
}

export interface ArtifactUploadCreate {
  purpose: "model" | "gcode" | "revision" | "slicer" | "external_writeback";
  target_role: string;
  target_id?: string | null;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  model_name?: string;
  collection?: string;
  tags?: string;
  source_hash?: string;
  target_library_id?: number;
}

export interface ArtifactUploadPlan {
  session_id: string;
  mode: ArtifactUploadMode;
  chunk_size: number;
  max_parallel: number;
  upload_path: string;
  uploaded_parts: ArtifactUploadPart[];
  expires_at: string;
}

type ArtifactUploadJsonBody =
  | ArtifactUploadCreate
  | { checksum_sha256: string }
  | { size_bytes: number; checksum_sha256: string; etag: string };

async function jsonRequest<T>(
  path: string,
  method: "POST",
  body?: ArtifactUploadJsonBody,
): Promise<T> {
  const response = await fetch(getUrl(path), {
    method,
    headers: body === undefined ? authHeaders() : jsonHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return handleResponse<T>(response);
}

export function createArtifactUpload(body: ArtifactUploadCreate): Promise<ArtifactUploadStatus> {
  return jsonRequest("/api/v1/artifact-uploads", "POST", body);
}

export async function getArtifactUpload(id: string): Promise<ArtifactUploadStatus> {
  const response = await fetch(getUrl(`/api/v1/artifact-uploads/${id}`), {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handleResponse<ArtifactUploadStatus>(response);
}

export async function getArtifactUploadPlan(id: string): Promise<ArtifactUploadPlan> {
  const response = await fetch(getUrl(`/api/v1/artifact-uploads/${id}/plan`), {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handleResponse<ArtifactUploadPlan>(response);
}

export async function putArtifactUploadChunk(
  id: string,
  index: number,
  offset: number,
  bytes: Blob,
  sha256: string,
  signal?: AbortSignal,
): Promise<ArtifactUploadStatus> {
  const query = new URLSearchParams({
    offset: String(offset),
    length: String(bytes.size),
    sha256,
  });
  const response = await fetch(getUrl(`/api/v1/artifact-uploads/${id}/chunks/${index}?${query}`), {
    method: "PUT",
    headers: authHeaders(),
    body: bytes,
    signal,
  });
  const result = await handleResponse<{ session: ArtifactUploadStatus }>(response);
  return result.session;
}

export function signArtifactUploadPart(
  id: string,
  partNumber: number,
  checksumSha256: string,
): Promise<{ url: string; method: "PUT"; headers: Record<string, string>; expires_at: string }> {
  return jsonRequest(`/api/v1/artifact-uploads/${id}/parts/${partNumber}/sign`, "POST", {
    checksum_sha256: checksumSha256,
  });
}

export function recordArtifactUploadPart(
  id: string,
  partNumber: number,
  body: { size_bytes: number; checksum_sha256: string; etag: string },
): Promise<{ session: ArtifactUploadStatus; part: ArtifactUploadPart }> {
  return jsonRequest(`/api/v1/artifact-uploads/${id}/parts/${partNumber}`, "POST", body);
}

export function finalizeArtifactUpload(id: string): Promise<ArtifactUploadStatus> {
  return jsonRequest(`/api/v1/artifact-uploads/${id}/finalize`, "POST");
}

export async function abortArtifactUpload(id: string): Promise<ArtifactUploadStatus> {
  const response = await fetch(getUrl(`/api/v1/artifact-uploads/${id}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handleResponse<ArtifactUploadStatus>(response);
}
