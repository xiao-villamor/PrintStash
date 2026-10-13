import { parseApiError } from "@/lib/errors";
import { withSessionRequest } from "@/lib/session-transport";
import { sha256 } from "@noble/hashes/sha2.js";

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

function toHex(bytes: ArrayBuffer | Uint8Array): string {
  return Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, "0")).join("");
}

const FALLBACK_HASH_CHUNK_BYTES = 8 * 1024 * 1024;

// `crypto.subtle` only exists in secure contexts, so a self-hosted Vault opened over
// plain HTTP on a LAN address (http://192.168.x.x:3000) has none. Hash in JS there,
// streaming the Blob so a large mesh is never held twice.
export async function sha256Blob(blob: Blob): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle) return toHex(await subtle.digest("SHA-256", await blob.arrayBuffer()));
  const hash = sha256.create();
  for (let offset = 0; offset < blob.size; offset += FALLBACK_HASH_CHUNK_BYTES) {
    const chunk = blob.slice(offset, offset + FALLBACK_HASH_CHUNK_BYTES);
    hash.update(new Uint8Array(await chunk.arrayBuffer()));
  }
  return toHex(hash.digest());
}

export interface UploadOptions {
  signal?: AbortSignal;
  onProgress?: (progress: ArtifactUploadProgress) => void;
  onSession?: (id: string) => void;
  digest?: (blob: Blob) => Promise<string>;
  api?: ArtifactUploadApi;
  /** Waits out one busy refusal; replaced in tests so no real time passes. */
  pause?: (milliseconds: number) => Promise<void>;
}

/** Longest a queued file waits for the server to have room before it fails. */
const BUSY_WAIT_LIMIT_MS = 10 * 60_000;
const BUSY_WAIT_STEPS_MS = [500, 1_000, 2_000, 4_000];

const pauseFor = (milliseconds: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

/**
 * The server refuses new upload work while its staging area or request budget
 * is full. That is a "not yet", so a queued file waits its turn; every other
 * refusal is an answer and is reported at once.
 */
function isBusyRefusal(cause: unknown): boolean {
  const error = parseApiError(cause);
  return (
    (error.status === 429 && error.code === "rate_limited") ||
    error.code === "staging_capacity_exceeded"
  );
}

async function whenAdmitted<T>(
  attempt: () => Promise<T>,
  assertCurrent: () => void,
  pause: (milliseconds: number) => Promise<void>,
): Promise<T> {
  let waited = 0;
  for (let refusals = 0; ; refusals += 1) {
    try {
      return await attempt();
    } catch (cause) {
      if (!isBusyRefusal(cause) || waited >= BUSY_WAIT_LIMIT_MS) throw cause;
    }
    const step = BUSY_WAIT_STEPS_MS[Math.min(refusals, BUSY_WAIT_STEPS_MS.length - 1)];
    await pause(step);
    waited += step;
    assertCurrent();
  }
}

const activeControllers = new Map<string, AbortController>();

function controlledOptions(id: string, options: UploadOptions): UploadOptions {
  if (options.signal) return options;
  const controller = new AbortController();
  activeControllers.set(id, controller);
  return { ...options, signal: controller.signal };
}

function releaseController(id: string, signal: AbortSignal | undefined): void {
  if (activeControllers.get(id)?.signal === signal) activeControllers.delete(id);
}

export function pauseArtifactUpload(id: string): boolean {
  const controller = activeControllers.get(id);
  if (!controller) return false;
  controller.abort(new DOMException("Upload paused", "AbortError"));
  activeControllers.delete(id);
  return true;
}

export function isArtifactUploadActive(id: string): boolean {
  return activeControllers.has(id);
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
  return withSessionRequest(async (request) => {
    const digest = options.digest ?? sha256Blob;
    const api = options.api ?? defaultArtifactUploadApi;
    const plan = await api.getArtifactUploadPlan(session.id);
    request.assertCurrent();
    const uploaded = new Set(plan.uploaded_parts.map((part) => part.index));
    let transferred = plan.uploaded_parts.reduce((total, part) => total + part.size_bytes, 0);
    const parts = Math.ceil(file.size / plan.chunk_size);
    for (let index = 0; index < parts; index += 1) {
      if (uploaded.has(index)) continue;
      request.assertCurrent();
      const offset = index * plan.chunk_size;
      const bytes = file.slice(offset, Math.min(file.size, offset + plan.chunk_size));
      const checksum = await digest(bytes);
      request.assertCurrent();
      if (plan.mode === "native_parts") {
        const instruction = await api.signArtifactUploadPart(session.id, index + 1, checksum);
        request.assertCurrent();
        const response = await fetch(instruction.url, {
          method: instruction.method,
          headers: instruction.headers,
          body: bytes,
          signal: request.signal,
        });
        request.assertCurrent();
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
          request.signal,
        );
      }
      request.assertCurrent();
      transferred += bytes.size;
      options.onProgress?.({
        phase: "transferring",
        transferredBytes: transferred,
        totalBytes: file.size,
      });
    }
    request.assertCurrent();
    options.onProgress?.({
      phase: "verifying",
      transferredBytes: file.size,
      totalBytes: file.size,
    });
    request.assertCurrent();
    const finalized = await whenAdmitted(
      () => api.finalizeArtifactUpload(session.id),
      request.assertCurrent,
      options.pause ?? pauseFor,
    );
    request.assertCurrent();
    options.onProgress?.({
      phase: finalized.state === "completed" ? "completed" : "ingesting",
      transferredBytes: file.size,
      totalBytes: file.size,
    });
    request.assertCurrent();
    if (finalized.state === "completed") forgetArtifactUpload(finalized.id);
    return finalized;
  }, options.signal);
}

export async function uploadArtifact(
  file: File,
  request: Omit<ArtifactUploadCreate, "filename" | "media_type" | "size_bytes" | "sha256">,
  options: UploadOptions = {},
): Promise<ArtifactUploadStatus> {
  return withSessionRequest(async (transport) => {
    const digest = options.digest ?? sha256Blob;
    const api = options.api ?? defaultArtifactUploadApi;
    options.onProgress?.({ phase: "hashing", transferredBytes: 0, totalBytes: file.size });
    const sha256 = await digest(file);
    transport.assertCurrent();
    const session = await whenAdmitted(
      () =>
        api.createArtifactUpload({
          ...request,
          filename: file.name,
          media_type: file.type || "application/octet-stream",
          size_bytes: file.size,
          sha256,
        }),
      transport.assertCurrent,
      options.pause ?? pauseFor,
    );
    transport.assertCurrent();
    rememberArtifactUpload(session.id);
    const controlled = controlledOptions(session.id, { ...options, digest });
    try {
      options.onSession?.(session.id);
      transport.assertCurrent();
      return await transfer(session, file, controlled);
    } finally {
      releaseController(session.id, controlled.signal);
    }
  }, options.signal);
}

export async function resumeArtifactUpload(
  id: string,
  file: File,
  options: UploadOptions = {},
): Promise<ArtifactUploadStatus> {
  return withSessionRequest(async (request) => {
    const api = options.api ?? defaultArtifactUploadApi;
    const session = await api.getArtifactUpload(id);
    request.assertCurrent();
    if (session.filename !== file.name || session.size_bytes !== file.size) {
      throw new Error("artifact_upload_file_mismatch");
    }
    request.assertCurrent();
    rememberArtifactUpload(id);
    const controlled = controlledOptions(id, options);
    try {
      options.onSession?.(id);
      request.assertCurrent();
      return await transfer(session, file, controlled);
    } finally {
      releaseController(id, controlled.signal);
    }
  }, options.signal);
}

export async function cancelArtifactUpload(
  id: string,
  api: ArtifactUploadApi = defaultArtifactUploadApi,
): Promise<ArtifactUploadStatus> {
  return withSessionRequest(async (request) => {
    pauseArtifactUpload(id);
    const session = await api.abortArtifactUpload(id);
    request.assertCurrent();
    forgetArtifactUpload(id);
    return session;
  });
}
