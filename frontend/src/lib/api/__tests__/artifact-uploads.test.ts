/** Canonical upload creation carries a stable retry identity without provider state. */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  abortArtifactUpload,
  createArtifactUpload,
  finalizeArtifactUpload,
  getArtifactUpload,
  getArtifactUploadPlan,
  putArtifactUploadChunk,
  recordArtifactUploadPart,
  signArtifactUploadPart,
  type ArtifactUploadPlan,
  type ArtifactUploadStatus,
} from "@/lib/api/artifact-uploads";

const session: ArtifactUploadStatus = {
  id: "session-1",
  purpose: "model",
  target_role: "new_model",
  target_id: null,
  filename: "part.stl",
  media_type: "model/stl",
  size_bytes: 4,
  state: "uploading",
  mode: "simple",
  received_bytes: 0,
  verified_size: null,
  verified_sha256: null,
  job_id: null,
  retryable: false,
  error_code: null,
  created_at: "2026-09-09T00:00:00Z",
  updated_at: "2026-09-09T00:00:00Z",
  expires_at: "2026-09-10T00:00:00Z",
  parts: [],
};

type JsonFixture =
  | ArtifactUploadStatus
  | ArtifactUploadPlan
  | { session: ArtifactUploadStatus }
  | Awaited<ReturnType<typeof signArtifactUploadPart>>
  | Awaited<ReturnType<typeof recordArtifactUploadPart>>;

const json = (body: JsonFixture) =>
  new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("artifact upload API", () => {
  it("binds creation retries to the declared representation hash", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(json({ ...session, state: "created" }));
    vi.stubGlobal("fetch", fetchMock);

    await createArtifactUpload({
      purpose: "model",
      target_role: "new_model",
      filename: "part.stl",
      media_type: "model/stl",
      size_bytes: 4,
      sha256: "a".repeat(64),
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/artifact-uploads",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Idempotency-Key": `artifact-upload:${"a".repeat(64)}`,
        }),
      }),
    );
  });

  it("reads resumable session state without caching", async () => {
    const plan = {
      session_id: session.id,
      mode: "simple" as const,
      chunk_size: 8 * 1024 * 1024,
      max_parallel: 1,
      upload_path: `/api/v1/artifact-uploads/${session.id}/chunks/{index}`,
      uploaded_parts: [],
      expires_at: session.expires_at,
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(json(plan));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getArtifactUpload(session.id)).resolves.toEqual(session);
    await expect(getArtifactUploadPlan(session.id)).resolves.toEqual(plan);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/v1/artifact-uploads/${session.id}`,
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/v1/artifact-uploads/${session.id}/plan`,
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("sends the bounded chunk envelope", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(json({ session }));
    vi.stubGlobal("fetch", fetchMock);
    const bytes = new Blob(["part"], { type: "model/stl" });
    const signal = new AbortController().signal;

    await expect(
      putArtifactUploadChunk(session.id, 2, 8, bytes, "b".repeat(64), signal),
    ).resolves.toEqual(session);

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/artifact-uploads/${session.id}/chunks/2?offset=8&length=4&sha256=${"b".repeat(64)}`,
      expect.objectContaining({ method: "PUT", body: bytes, signal }),
    );
  });

  it("maps native part operations", async () => {
    const instruction = {
      url: "https://objects.example.test/part",
      method: "PUT" as const,
      headers: { "x-amz-checksum-sha256": "checksum" },
      expires_at: session.expires_at,
    };
    const receipt = {
      session,
      part: { index: 0, offset: 0, size_bytes: 4, sha256: "c".repeat(64) },
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(instruction))
      .mockResolvedValueOnce(json(receipt));
    vi.stubGlobal("fetch", fetchMock);

    await expect(signArtifactUploadPart(session.id, 1, "c".repeat(64))).resolves.toEqual(
      instruction,
    );
    await expect(
      recordArtifactUploadPart(session.id, 1, {
        size_bytes: 4,
        checksum_sha256: "c".repeat(64),
        etag: '"part-1"',
      }),
    ).resolves.toEqual(receipt);

    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/artifact-uploads/${session.id}/parts/1/sign`);
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/v1/artifact-uploads/${session.id}/parts/1`);
  });

  it("maps terminal session operations", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ ...session, state: "ingesting" }))
      .mockResolvedValueOnce(json({ ...session, state: "aborted" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(finalizeArtifactUpload(session.id)).resolves.toMatchObject({
      state: "ingesting",
    });
    await expect(abortArtifactUpload(session.id)).resolves.toMatchObject({ state: "aborted" });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/v1/artifact-uploads/${session.id}/finalize`,
      expect.objectContaining({ method: "POST", body: undefined }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/v1/artifact-uploads/${session.id}`,
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
