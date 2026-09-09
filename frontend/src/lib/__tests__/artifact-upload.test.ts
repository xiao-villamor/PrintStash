/**
 * The upload coordinator owns browser resume state and provider-neutral plans.
 * A failure here can persist signed authority or restart already-received bytes.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelArtifactUpload,
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

    await uploadArtifact(
      new File(["12345678"], "part.stl", { type: "model/stl" }),
      { purpose: "model", target_role: "new_model" },
      { api, digest, onProgress: ({ phase }) => phases.push(phase) },
    );

    expect(rememberedArtifactUploads()).toEqual(["session-1"]);
    expect(localStorage.getItem("printstash.artifact-upload-session-ids")).toBe("session-1");
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
      undefined,
    );
    expect(rememberedArtifactUploads()).toEqual([]);
  });

  it("cancels server state and forgets resume state", async () => {
    const api = anApi();
    localStorage.setItem("printstash.artifact-upload-session-ids", "session-1");
    vi.mocked(api.abortArtifactUpload).mockResolvedValue(aSession({ state: "aborted" }));

    const result = await cancelArtifactUpload("session-1", api);

    expect(result.state).toBe("aborted");
    expect(rememberedArtifactUploads()).toEqual([]);
  });
});
