import {
  abortArtifactUpload,
  createArtifactUpload,
  finalizeArtifactUpload,
  getArtifactUpload,
  getArtifactUploadPlan,
  putArtifactUploadChunk,
  recordArtifactUploadPart,
  signArtifactUploadPart,
  type ArtifactUploadCreate,
  type ArtifactUploadStatus,
} from "@/lib/api/artifact-uploads";

export type ArtifactUploadPhase =
  | "hashing"
  | "transferring"
  | "verifying"
  | "ingesting"
  | "completed";

export interface ArtifactUploadProgress {
  phase: ArtifactUploadPhase;
  transferredBytes: number;
  totalBytes: number;
}

const STORAGE_KEY = "printstash.artifact-upload-session-ids";

function readIds(): string[] {
  if (!("localStorage" in globalThis)) return [];
  return (localStorage.getItem(STORAGE_KEY) ?? "").split("\n").filter(Boolean).slice(-50);
}

function writeIds(ids: string[]): void {
  if (!("localStorage" in globalThis)) return;
  localStorage.setItem(STORAGE_KEY, [...new Set(ids)].slice(-50).join("\n"));
}

export function rememberArtifactUpload(id: string): void {
  writeIds([...readIds(), id]);
}

export function forgetArtifactUpload(id: string): void {
  writeIds(readIds().filter((candidate) => candidate !== id));
}

export function rememberedArtifactUploads(): string[] {
  return readIds();
}

function toHex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, "0")).join("");
}

export async function sha256Blob(blob: Blob): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
}

interface UploadOptions {
  signal?: AbortSignal;
  onProgress?: (progress: ArtifactUploadProgress) => void;
  digest?: (blob: Blob) => Promise<string>;
  api?: ArtifactUploadApi;
}

export interface ArtifactUploadApi {
  abortArtifactUpload: typeof abortArtifactUpload;
  createArtifactUpload: typeof createArtifactUpload;
  finalizeArtifactUpload: typeof finalizeArtifactUpload;
  getArtifactUpload: typeof getArtifactUpload;
  getArtifactUploadPlan: typeof getArtifactUploadPlan;
  putArtifactUploadChunk: typeof putArtifactUploadChunk;
  recordArtifactUploadPart: typeof recordArtifactUploadPart;
  signArtifactUploadPart: typeof signArtifactUploadPart;
}

export const defaultArtifactUploadApi: ArtifactUploadApi = {
  abortArtifactUpload,
  createArtifactUpload,
  finalizeArtifactUpload,
  getArtifactUpload,
  getArtifactUploadPlan,
  putArtifactUploadChunk,
  recordArtifactUploadPart,
  signArtifactUploadPart,
};

async function transfer(
  session: ArtifactUploadStatus,
  file: File,
  options: UploadOptions,
): Promise<ArtifactUploadStatus> {
  const digest = options.digest ?? sha256Blob;
  const api = options.api ?? defaultArtifactUploadApi;
  const plan = await api.getArtifactUploadPlan(session.id);
  const uploaded = new Set(plan.uploaded_parts.map((part) => part.index));
  let transferred = plan.uploaded_parts.reduce((total, part) => total + part.size_bytes, 0);
  const parts = Math.ceil(file.size / plan.chunk_size);
  for (let index = 0; index < parts; index += 1) {
    if (uploaded.has(index)) continue;
    if (options.signal?.aborted) throw new DOMException("Upload paused", "AbortError");
    const offset = index * plan.chunk_size;
    const bytes = file.slice(offset, Math.min(file.size, offset + plan.chunk_size));
    const checksum = await digest(bytes);
    if (plan.mode === "native_parts") {
      const instruction = await api.signArtifactUploadPart(session.id, index + 1, checksum);
      const response = await fetch(instruction.url, {
        method: instruction.method,
        headers: instruction.headers,
        body: bytes,
        signal: options.signal,
      });
      if (!response.ok) throw new Error("artifact_upload_part_failed");
      const etag = response.headers.get("etag");
      if (!etag) throw new Error("artifact_upload_receipt_missing");
      session = (
        await api.recordArtifactUploadPart(session.id, index + 1, {
          size_bytes: bytes.size,
          checksum_sha256: checksum,
          etag,
        })
      ).session;
    } else {
      session = await api.putArtifactUploadChunk(
        session.id,
        index,
        offset,
        bytes,
        checksum,
        options.signal,
      );
    }
    transferred += bytes.size;
    options.onProgress?.({
      phase: "transferring",
      transferredBytes: transferred,
      totalBytes: file.size,
    });
  }
  options.onProgress?.({
    phase: "verifying",
    transferredBytes: file.size,
    totalBytes: file.size,
  });
  const finalized = await api.finalizeArtifactUpload(session.id);
  options.onProgress?.({
    phase: finalized.state === "completed" ? "completed" : "ingesting",
    transferredBytes: file.size,
    totalBytes: file.size,
  });
  if (finalized.state === "completed") forgetArtifactUpload(finalized.id);
  return finalized;
}

export async function uploadArtifact(
  file: File,
  request: Omit<ArtifactUploadCreate, "filename" | "media_type" | "size_bytes" | "sha256">,
  options: UploadOptions = {},
): Promise<ArtifactUploadStatus> {
  const digest = options.digest ?? sha256Blob;
  const api = options.api ?? defaultArtifactUploadApi;
  options.onProgress?.({ phase: "hashing", transferredBytes: 0, totalBytes: file.size });
  const sha256 = await digest(file);
  const session = await api.createArtifactUpload({
    ...request,
    filename: file.name,
    media_type: file.type || "application/octet-stream",
    size_bytes: file.size,
    sha256,
  });
  rememberArtifactUpload(session.id);
  return transfer(session, file, { ...options, digest });
}

export async function resumeArtifactUpload(
  id: string,
  file: File,
  options: UploadOptions = {},
): Promise<ArtifactUploadStatus> {
  const api = options.api ?? defaultArtifactUploadApi;
  const session = await api.getArtifactUpload(id);
  if (session.filename !== file.name || session.size_bytes !== file.size) {
    throw new Error("artifact_upload_file_mismatch");
  }
  rememberArtifactUpload(id);
  return transfer(session, file, options);
}

export async function cancelArtifactUpload(
  id: string,
  api: ArtifactUploadApi = defaultArtifactUploadApi,
): Promise<ArtifactUploadStatus> {
  const session = await api.abortArtifactUpload(id);
  forgetArtifactUpload(id);
  return session;
}
