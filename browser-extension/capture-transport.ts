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

interface CaptureUploadSlot {
  id: string;
  role: "file" | "cover";
  source_file_id: string | null;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

interface CaptureSlotResponse {
  item: { id: number };
  slots: CaptureUploadSlot[];
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
}): Promise<unknown> {
  const base = vault.replace(/\/$/, "");
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
    if (!created.ok)
      throw new Error(`PrintStash returned ${created.status} while creating upload slots.`);
    return (await created.json()) as CaptureSlotResponse;
  };
  const payload = await (runStage ? runStage("slot_create", createSlot) : createSlot());
  if (!payload?.item || !Array.isArray(payload.slots) || payload.slots.length !== uploads.length) {
    throw new Error("PrintStash returned invalid capture upload slots.");
  }

  for (const upload of uploads) {
    const slot = matchingSlot(payload.slots, upload);
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
      if (!uploaded.ok)
        throw new Error(
          `PrintStash returned ${uploaded.status} while uploading ${upload.declaration.filename}.`,
        );
    };
    await (runStage ? runStage("slot_upload", uploadSlot) : uploadSlot());
  }

  const finalize = async (signal?: AbortSignal) => {
    const finalized = await fetchImpl(
      `${base}/api/v1/inbox/${payload.item.id}/capture-upload-finalize`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${authorization}` },
        signal,
      },
    );
    if (!finalized.ok)
      throw new Error(`PrintStash returned ${finalized.status} while finalizing the capture.`);
    return finalized.json();
  };
  return runStage ? runStage("slot_finalize", finalize) : finalize();
}
