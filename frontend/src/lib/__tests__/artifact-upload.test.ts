/**
 * The upload coordinator owns browser resume state and provider-neutral plans.
 * A failure here can persist signed authority or restart already-received bytes.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelArtifactUpload,
  pauseArtifactUpload,
  rememberedArtifactUploads,
  resumeArtifactUpload,
  uploadArtifact,
  type ArtifactUploadApi,
} from "@/lib/artifact-upload";
import type { ArtifactUploadStatus } from "@/lib/api/artifact-uploads";

const NOW = "2026-09-09T00:00:00Z";

function aSession(overrides: Partial<ArtifactUploadStatus> = {}): ArtifactUploadStatus {
  return {
    id: "session-1",
    purpose: "model",
    target_role: "new_model",
    target_id: null,
    filename: "part.stl",
    media_type: "model/stl",
    size_bytes: 8,
    state: "uploading",
    mode: "api_chunks",
    received_bytes: 0,
    verified_size: null,
    verified_sha256: null,
    job_id: null,
    retryable: false,
    error_code: null,
    created_at: NOW,
    updated_at: NOW,
    expires_at: NOW,
    parts: [],
    ...overrides,
  };
}

function anApi(): ArtifactUploadApi {
  return {
    abortArtifactUpload: vi.fn<ArtifactUploadApi["abortArtifactUpload"]>(),
    createArtifactUpload: vi.fn<ArtifactUploadApi["createArtifactUpload"]>(),
    finalizeArtifactUpload: vi.fn<ArtifactUploadApi["finalizeArtifactUpload"]>(),
    getArtifactUpload: vi.fn<ArtifactUploadApi["getArtifactUpload"]>(),
    getArtifactUploadPlan: vi.fn<ArtifactUploadApi["getArtifactUploadPlan"]>(),
    putArtifactUploadChunk: vi.fn<ArtifactUploadApi["putArtifactUploadChunk"]>(),
    recordArtifactUploadPart: vi.fn<ArtifactUploadApi["recordArtifactUploadPart"]>(),
    signArtifactUploadPart: vi.fn<ArtifactUploadApi["signArtifactUploadPart"]>(),
  };
}

const digest = async (blob: Blob) => (blob.size === 8 ? "a" : "b").repeat(64);

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

afterEach(() => vi.unstubAllGlobals());

describe("uploadArtifact", () => {
  it("persists only the opaque session id", async () => {
    const api = anApi();
    vi.mocked(api.createArtifactUpload).mockResolvedValue(aSession());
    vi.mocked(api.getArtifactUploadPlan).mockResolvedValue({
      session_id: "session-1",
      mode: "api_chunks",
      chunk_size: 4,
      max_parallel: 2,
      upload_path: "/opaque/chunks/{index}",
      uploaded_parts: [],
      expires_at: NOW,
    });
    vi.mocked(api.putArtifactUploadChunk).mockResolvedValue(aSession());
    vi.mocked(api.finalizeArtifactUpload).mockResolvedValue(
      aSession({ state: "ingesting", job_id: "job-1" }),
    );
    const phases: string[] = [];
    const sessions: string[] = [];
    const controller = new AbortController();

    await uploadArtifact(
      new File(["12345678"], "part.stl", { type: "model/stl" }),
      { purpose: "model", target_role: "new_model" },
      {
        api,
        digest,
        signal: controller.signal,
        onSession: (id) => sessions.push(id),
        onProgress: ({ phase }) => phases.push(phase),
      },
    );

    expect(rememberedArtifactUploads()).toEqual(["session-1"]);
    expect(localStorage.getItem("printstash.artifact-upload-session-ids")).toBe("session-1");
    expect(sessions).toEqual(["session-1"]);
    expect(phases).toEqual(["hashing", "transferring", "transferring", "verifying", "ingesting"]);
  });

  it("resumes from a fresh plan without repeating received parts", async () => {
    const api = anApi();
    vi.mocked(api.getArtifactUpload).mockResolvedValue(aSession());
    vi.mocked(api.getArtifactUploadPlan).mockResolvedValue({
      session_id: "session-1",
      mode: "api_chunks",
      chunk_size: 4,
      max_parallel: 2,
      upload_path: "/opaque/chunks/{index}",
      uploaded_parts: [{ index: 0, offset: 0, size_bytes: 4, sha256: "b".repeat(64) }],
      expires_at: NOW,
    });
    vi.mocked(api.putArtifactUploadChunk).mockResolvedValue(aSession());
    vi.mocked(api.finalizeArtifactUpload).mockResolvedValue(aSession({ state: "completed" }));

    await resumeArtifactUpload("session-1", new File(["12345678"], "part.stl"), {
      api,
      digest,
    });

    expect(api.getArtifactUploadPlan).toHaveBeenCalledWith("session-1");
    expect(api.putArtifactUploadChunk).toHaveBeenCalledOnce();
    expect(api.putArtifactUploadChunk).toHaveBeenCalledWith(
      "session-1",
      1,
      4,
      expect.any(Blob),
      "b".repeat(64),
      expect.any(AbortSignal),
    );
    expect(rememberedArtifactUploads()).toEqual([]);
  });

  it("forgets resume state after cancelling on the server", async () => {
    const api = anApi();
    localStorage.setItem("printstash.artifact-upload-session-ids", "session-1");
    vi.mocked(api.abortArtifactUpload).mockResolvedValue(aSession({ state: "aborted" }));

    const result = await cancelArtifactUpload("session-1", api);

    expect(result.state).toBe("aborted");
    expect(rememberedArtifactUploads()).toEqual([]);
  });

  it("sends native part bytes only to the short-lived signed URL", async () => {
    const api = anApi();
    vi.mocked(api.createArtifactUpload).mockResolvedValue(aSession({ mode: "native_parts" }));
    vi.mocked(api.getArtifactUploadPlan).mockResolvedValue({
      session_id: "session-1",
      mode: "native_parts",
      chunk_size: 8,
      max_parallel: 1,
      upload_path: "/opaque/parts/{part_number}",
      uploaded_parts: [],
      expires_at: NOW,
    });
    vi.mocked(api.signArtifactUploadPart).mockResolvedValue({
      url: "https://objects.example.test/private-part?signature=temporary",
      method: "PUT",
      headers: { "x-checksum": "required" },
      expires_at: NOW,
    });
    vi.mocked(api.recordArtifactUploadPart).mockResolvedValue({
      session: aSession({ mode: "native_parts", received_bytes: 8 }),
      part: { index: 0, offset: 0, size_bytes: 8, sha256: "a".repeat(64) },
    });
    vi.mocked(api.finalizeArtifactUpload).mockResolvedValue(
      aSession({ mode: "native_parts", state: "ingesting", job_id: "job-1" }),
    );
    const directFetch = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 200, headers: { etag: '"part-1"' } }));
    vi.stubGlobal("fetch", directFetch);

    await uploadArtifact(
      new File(["12345678"], "part.stl", { type: "model/stl" }),
      { purpose: "model", target_role: "new_model" },
      { api, digest },
    );

    expect(directFetch).toHaveBeenCalledOnce();
    expect(directFetch.mock.calls[0][0]).toContain("objects.example.test/private-part");
    expect(api.putArtifactUploadChunk).not.toHaveBeenCalled();
    expect(api.recordArtifactUploadPart).toHaveBeenCalledWith("session-1", 1, {
      size_bytes: 8,
      checksum_sha256: "a".repeat(64),
      etag: '"part-1"',
    });
    expect(localStorage.getItem("printstash.artifact-upload-session-ids")).toBe("session-1");
  });

  it("pauses an active transfer without aborting its durable server session", async () => {
    const api = anApi();
    vi.mocked(api.createArtifactUpload).mockResolvedValue(aSession());
    vi.mocked(api.getArtifactUploadPlan).mockResolvedValue({
      session_id: "session-1",
      mode: "api_chunks",
      chunk_size: 8,
      max_parallel: 1,
      upload_path: "/opaque/chunks/{index}",
      uploaded_parts: [],
      expires_at: NOW,
    });
    vi.mocked(api.putArtifactUploadChunk).mockImplementation(
      (_id, _index, _offset, _bytes, _checksum, signal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(signal.reason ?? new DOMException("Upload paused", "AbortError")),
          );
        }),
    );
    let sessionId: string | undefined;
    const transfer = uploadArtifact(
      new File(["12345678"], "part.stl", { type: "model/stl" }),
      { purpose: "model", target_role: "new_model" },
      { api, digest, onSession: (id) => (sessionId = id) },
    );
    await vi.waitFor(() => expect(sessionId).toBe("session-1"));

    expect(pauseArtifactUpload("session-1")).toBe(true);

    await expect(transfer).rejects.toMatchObject({ name: "AbortError" });
    expect(api.abortArtifactUpload).not.toHaveBeenCalled();
    expect(rememberedArtifactUploads()).toEqual(["session-1"]);
  });
});
