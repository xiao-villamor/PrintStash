import { runCaptureRequest } from "./capture-operation.ts";
import { CaptureAuthenticationError } from "./core.ts";

import type { CaptureSourceDraft } from "./capture-adapter.ts";

export interface BrowserCaptureFile {
  id: string;
  file: Blob;
  filename: string;
  mediaType: string;
  role?: "file" | "cover";
}

export const CAPTURE_MAX_FILE_SIZE_BYTES = 512 * 1024 * 1024;
export const CAPTURE_MAX_TOTAL_SIZE_BYTES = 1024 * 1024 * 1024;
export const CAPTURE_MAX_FILES = 64;
export const CAPTURE_CLEANUP_TIMEOUT_MS = 5_000;

interface CaptureUploadSlot {
  id: string;
  role: "file" | "cover";
  source_file_id: string | null;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

function captureReceipt(payload: unknown): { itemId: number; slots: unknown } {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("item" in payload) ||
    typeof payload.item !== "object" ||
    payload.item === null ||
    !("id" in payload.item) ||
    typeof payload.item.id !== "number" ||
    !Number.isSafeInteger(payload.item.id) ||
    payload.item.id <= 0
  ) {
    throw new Error("PrintStash returned invalid capture upload slots.");
  }
  return { itemId: payload.item.id, slots: "slots" in payload ? payload.slots : undefined };
}

function isCaptureSlot(slot: unknown): slot is CaptureUploadSlot {
  return (
    typeof slot === "object" &&
    slot !== null &&
    "id" in slot &&
    typeof slot.id === "string" &&
    slot.id.trim().length > 0 &&
    "role" in slot &&
    (slot.role === "file" || slot.role === "cover") &&
    "source_file_id" in slot &&
    (slot.source_file_id === null || typeof slot.source_file_id === "string") &&
    "filename" in slot &&
    typeof slot.filename === "string" &&
    "media_type" in slot &&
    typeof slot.media_type === "string" &&
    "size_bytes" in slot &&
    typeof slot.size_bytes === "number" &&
    Number.isSafeInteger(slot.size_bytes) &&
    slot.size_bytes >= 0 &&
    "sha256" in slot &&
    typeof slot.sha256 === "string"
  );
}

function captureSlots(value: unknown, count: number): CaptureUploadSlot[] {
  if (
    !Array.isArray(value) ||
    value.length !== count ||
    !value.every(isCaptureSlot) ||
    new Set(value.map((slot) => slot.id)).size !== count
  ) {
    throw new Error("PrintStash returned invalid capture upload slots.");
  }
  return value;
}

interface DeclaredCaptureFile {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

interface PreparedCaptureFile {
  declaration: DeclaredCaptureFile;
  file: Blob;
  role: "file" | "cover";
}

export type CaptureUploadStage = "slot_create" | "slot_upload" | "slot_finalize";

export class CaptureCapacityError extends Error {
  constructor(readonly reason: "staging_capacity_exceeded" | "staging_capacity_unavailable") {
    super(
      reason === "staging_capacity_unavailable"
        ? "PrintStash could not measure staging space. Check its staging directory and disk access."
        : "PrintStash capture capacity is full. Clear completed Pending Imports or free staging space, then retry.",
    );
  }
}

export type CaptureStageRunner = <T>(
  stage: CaptureUploadStage,
  operation: (signal: AbortSignal) => Promise<T>,
) => Promise<T>;

async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function prepareCaptureFile(
  upload: BrowserCaptureFile,
  id: string,
  role: "file" | "cover",
): Promise<PreparedCaptureFile> {
  if (
    !Number.isSafeInteger(upload.file.size) ||
    upload.file.size < 0 ||
    upload.file.size > CAPTURE_MAX_FILE_SIZE_BYTES
  ) {
    throw new Error(`Capture file ${upload.filename} exceeds the supported size limit.`);
  }
  return {
    declaration: {
      id,
      filename: upload.filename,
      media_type: upload.mediaType,
      size_bytes: upload.file.size,
      sha256: await sha256Hex(upload.file),
    },
    file: upload.file,
    role,
  };
}

function matchingSlot(slots: CaptureUploadSlot[], upload: PreparedCaptureFile): CaptureUploadSlot {
  const slot = slots.find((candidate) =>
    upload.role === "cover"
      ? candidate.role === "cover"
      : candidate.role === "file" && candidate.source_file_id === upload.declaration.id,
  );
  if (
    slot === undefined ||
    slot.filename !== upload.declaration.filename ||
    slot.media_type !== upload.declaration.media_type ||
    slot.size_bytes !== upload.declaration.size_bytes ||
    slot.sha256 !== upload.declaration.sha256
  ) {
    throw new Error("PrintStash returned invalid capture upload slots.");
  }
  return slot;
}

export async function captureRichFiles({
  fetchImpl = fetch,
  vault,
  authorization,
  sourceUrl,
  title,
  captureSource,
  files,
  cover,
  runStage,
  signal,
}: {
  fetchImpl?: typeof fetch;
  vault: string;
  authorization: string;
  sourceUrl: string;
  title?: string;
  captureSource: CaptureSourceDraft;
  files: BrowserCaptureFile[];
  cover?: BrowserCaptureFile;
  runStage?: CaptureStageRunner;
  signal?: AbortSignal;
}): Promise<unknown> {
  const base = vault.replace(/\/$/, "");
  const stageRequest = <T>(
    stage: CaptureUploadStage,
    operation: (signal: AbortSignal) => Promise<T>,
  ) => {
    const scoped = (stageSignal?: AbortSignal) =>
      runCaptureRequest([signal, stageSignal], operation);
    return runStage ? runStage(stage, scoped) : scoped();
  };
  if (files.length === 0 || files.length > CAPTURE_MAX_FILES) {
    throw new Error("Capture file count is outside the supported limit.");
  }
  const ids = files.map((file) => file.id);
  if (ids.some((id) => !/^[a-zA-Z0-9._:-]{1,255}$/.test(id)) || new Set(ids).size !== ids.length) {
    throw new Error("Capture file IDs must be unique, bounded identifiers.");
  }
  const preparedFiles: PreparedCaptureFile[] = [];
  let totalBytes = 0;
  for (const upload of files) {
    const prepared = await prepareCaptureFile(upload, upload.id, "file");
    totalBytes += prepared.declaration.size_bytes;
    if (totalBytes > CAPTURE_MAX_TOTAL_SIZE_BYTES) {
      throw new Error("Capture files exceed the supported aggregate size limit.");
    }
    preparedFiles.push(prepared);
  }
  const preparedCover = cover ? await prepareCaptureFile(cover, "cover", "cover") : undefined;
  if (preparedCover) {
    totalBytes += preparedCover.declaration.size_bytes;
    if (totalBytes > CAPTURE_MAX_TOTAL_SIZE_BYTES) {
      throw new Error("Capture files exceed the supported aggregate size limit.");
    }
  }
  const uploads = preparedCover ? [...preparedFiles, preparedCover] : preparedFiles;
  const createSlot = async (signal?: AbortSignal) => {
    const created = await fetchImpl(`${base}/api/v1/inbox/capture-upload-slots`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authorization}`, "Content-Type": "application/json" },
      signal,
      body: JSON.stringify({
        source_url: sourceUrl,
        title: title || null,
        capture_source: captureSource,
        files: preparedFiles.map(({ declaration }) => declaration),
        ...(preparedCover ? { cover: preparedCover.declaration } : {}),
      }),
    });
    if (created.status === 401) throw new CaptureAuthenticationError();
    if (created.status === 507) {
      const detail = await created.json().catch(() => null);
      if (detail?.detail === "staging_capacity_unavailable")
        throw new CaptureCapacityError("staging_capacity_unavailable");
      throw new CaptureCapacityError("staging_capacity_exceeded");
    }
    if (!created.ok)
      throw new Error(`PrintStash returned ${created.status} while creating upload slots.`);
    return captureReceipt(await created.json());
  };
  const payload = await stageRequest("slot_create", createSlot);
  try {
    const slots = captureSlots(payload.slots, uploads.length);
    const plannedUploads = uploads.map((upload) => ({ upload, slot: matchingSlot(slots, upload) }));
    for (const { upload, slot } of plannedUploads) {
      const uploadSlot = async (signal?: AbortSignal) => {
        const uploaded = await fetchImpl(
          `${base}/api/v1/inbox/capture-upload-slots/${encodeURIComponent(slot.id)}`,
          {
            method: "PUT",
            headers: {
              Authorization: `Bearer ${authorization}`,
              "Content-Type": upload.declaration.media_type,
            },
            signal,
            body: upload.file,
          },
        );
        if (uploaded.status === 401) throw new CaptureAuthenticationError();
        if (!uploaded.ok)
          throw new Error(
            `PrintStash returned ${uploaded.status} while uploading ${upload.declaration.filename}.`,
          );
      };
      await stageRequest("slot_upload", uploadSlot);
    }

    const finalize = async (signal?: AbortSignal) => {
      const finalized = await fetchImpl(
        `${base}/api/v1/inbox/${payload.itemId}/capture-upload-finalize`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${authorization}` },
          signal,
        },
      );
      if (finalized.status === 401) throw new CaptureAuthenticationError();
      if (!finalized.ok)
        throw new Error(`PrintStash returned ${finalized.status} while finalizing the capture.`);
      return finalized.json();
    };
    return await stageRequest("slot_finalize", finalize);
  } catch (error) {
    // A failed transfer owns no reviewable capture. Ask the vault to dismiss
    // the exact item and release any slots already uploaded in this batch.
    const cleanup = new AbortController();
    const deadline = setTimeout(() => cleanup.abort(), CAPTURE_CLEANUP_TIMEOUT_MS);
    try {
      await runCaptureRequest([cleanup.signal], async (cleanupSignal) => {
        await fetchImpl(`${base}/api/v1/inbox/${payload.itemId}/capture-upload`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${authorization}` },
          signal: cleanupSignal,
        });
      });
    } catch {
      // Preserve the original upload failure. The item remains visible for
      // manual dismissal if the cleanup request could not reach the vault.
    } finally {
      clearTimeout(deadline);
    }
    throw error;
  }
}
