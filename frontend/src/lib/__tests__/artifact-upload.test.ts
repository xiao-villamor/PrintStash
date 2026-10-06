/**
 * The upload coordinator owns browser resume state and provider-neutral plans.
 * A failure here can persist signed authority or restart already-received bytes.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelArtifactUpload,
  isArtifactUploadActive,
  pauseArtifactUpload,
  rememberedArtifactUploads,
  resumeArtifactUpload,
  sha256Blob,
  uploadArtifact,
  type ArtifactUploadApi,
} from "@/lib/artifact-upload";
import { clearLogin, getUser, storeLogin } from "@/lib/auth-store";
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

    expect(api.createArtifactUpload).toHaveBeenCalledWith({
      purpose: "model",
      target_role: "new_model",
      filename: "part.stl",
      media_type: "model/stl",
      size_bytes: 8,
      sha256: "a".repeat(64),
    });
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

describe("sha256Blob", () => {
  // SHA-256("abc"), FIPS 180-2 appendix B.1.
  const ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

  // What a plain-HTTP LAN origin sees: `crypto` exists, `crypto.subtle` does not.
  const withoutSubtleCrypto = () =>
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });

  it("hashes with SubtleCrypto when the page is a secure context", async () => {
    await expect(sha256Blob(new Blob(["abc"]))).resolves.toBe(ABC);
  });

  it("hashes without SubtleCrypto on a plain-HTTP LAN origin", async () => {
    withoutSubtleCrypto();
    expect(globalThis.crypto.subtle).toBeUndefined();

    await expect(sha256Blob(new Blob(["abc"]))).resolves.toBe(ABC);
  });

  it("streams a Blob larger than one fallback chunk to the same digest", async () => {
    const bytes = new Uint8Array(8 * 1024 * 1024 + 3).map((_, index) => index % 251);
    const blob = new Blob([bytes]);
    const native = await sha256Blob(blob);
    withoutSubtleCrypto();

    await expect(sha256Blob(blob)).resolves.toBe(native);
  });
});

/** Authentication bounds the entire upload workflow, including local hashing and signed bytes. */
describe("upload session isolation", () => {
  it("stops an upload retired during hashing", async () => {
    const api = anApi();
    const hash = Promise.withResolvers<string>();
    const pending = uploadArtifact(
      new File(["12345678"], "part.stl"),
      { purpose: "model", target_role: "new_model" },
      { api, digest: () => hash.promise },
    );
    const outcome = pending.catch((error: Error) => error);
    clearLogin();
    hash.resolve("a".repeat(64));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(api.createArtifactUpload).not.toHaveBeenCalled();
    expect(rememberedArtifactUploads()).toEqual([]);
  });

  it("never records a signed-part receipt after session retirement", async () => {
    const api = anApi();
    vi.mocked(api.getArtifactUpload).mockResolvedValue(aSession({ mode: "native_parts" }));
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
    const headers = Promise.withResolvers<Response>();
    const fetcher = vi.fn<typeof fetch>().mockReturnValueOnce(headers.promise);
    vi.stubGlobal("fetch", fetcher);
    const pending = resumeArtifactUpload("session-1", new File(["12345678"], "part.stl"), {
      api,
      digest,
    });
    const outcome = pending.catch((error: Error) => error);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    clearLogin();
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    headers.resolve(new Response(null, { headers: { etag: '"part-1"' } }));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(api.recordArtifactUploadPart).not.toHaveBeenCalled();
    expect(api.finalizeArtifactUpload).not.toHaveBeenCalled();
  });
});

/** Expiring the cookie session must not erase the server's genuine upload rejection. */
describe("upload unauthorized failure", () => {
  it("preserves a genuine unauthorized upload error", async () => {
    storeLogin("", { id: 7, username: "maker", email: null, is_superuser: false });
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response('{"detail":"invalid_or_expired_token"}', { status: 401 })),
    );
    await expect(
      uploadArtifact(
        new File(["12345678"], "part.stl"),
        { purpose: "model", target_role: "new_model" },
        { digest },
      ),
    ).rejects.toMatchObject({ status: 401, code: "invalid_or_expired_token" });
    expect(getUser()).toBeNull();
  });
});

/** A retired upload failure remains cancellation, never a new account's expiry. */
describe("retired upload unauthorized failure", () => {
  it("ignores a retired upload's unauthorized response", async () => {
    storeLogin("", { id: 7, username: "maker", email: null, is_superuser: false });
    const headers = Promise.withResolvers<Response>();
    const fetcher = vi.fn<typeof fetch>().mockReturnValueOnce(headers.promise);
    vi.stubGlobal("fetch", fetcher);
    const pending = uploadArtifact(
      new File(["12345678"], "part.stl"),
      { purpose: "model", target_role: "new_model" },
      { digest },
    );
    const outcome = pending.catch((error: Error) => error);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false });
    headers.resolve(new Response('{"detail":"invalid_or_expired_token"}', { status: 401 }));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(getUser()?.id).toBe(9);
  });
});

/** Callback delivery belongs inside the transfer's cleanup boundary. */
describe("upload session callback lifetime", () => {
  it.each(["upload", "resume"] as const)(
    "releases the %s transfer when its session callback retires authentication",
    async (mode) => {
      const id = `retired-callback-${mode}`;
      const api = anApi();
      vi.mocked(api.createArtifactUpload).mockResolvedValue(aSession({ id }));
      vi.mocked(api.getArtifactUpload).mockResolvedValue(aSession({ id }));
      const file = new File(["12345678"], "part.stl");
      const options = { api, digest, onSession: () => clearLogin() };
      try {
        const pending =
          mode === "upload"
            ? uploadArtifact(file, { purpose: "model", target_role: "new_model" }, options)
            : resumeArtifactUpload(id, file, options);
        await expect(pending).rejects.toMatchObject({ name: "AbortError" });
        expect(isArtifactUploadActive(id)).toBe(false);
        expect(api.getArtifactUploadPlan).not.toHaveBeenCalled();
      } finally {
        pauseArtifactUpload(id);
      }
    },
  );

  it.each(["upload", "resume"] as const)(
    "releases the %s transfer when its session callback fails",
    async (mode) => {
      const id = `failed-callback-${mode}`;
      const api = anApi();
      vi.mocked(api.createArtifactUpload).mockResolvedValue(aSession({ id }));
      vi.mocked(api.getArtifactUpload).mockResolvedValue(aSession({ id }));
      const failure = new Error("callback failed");
      const file = new File(["12345678"], "part.stl");
      const options = {
        api,
        digest,
        onSession: () => {
          throw failure;
        },
      };
      try {
        const pending =
          mode === "upload"
            ? uploadArtifact(file, { purpose: "model", target_role: "new_model" }, options)
            : resumeArtifactUpload(id, file, options);
        await expect(pending).rejects.toBe(failure);
        expect(isArtifactUploadActive(id)).toBe(false);
        expect(api.getArtifactUploadPlan).not.toHaveBeenCalled();
      } finally {
        pauseArtifactUpload(id);
      }
    },
  );

  it.each(["upload", "resume"] as const)(
    "allows the %s session callback to pause its transfer",
    async (mode) => {
      const id = `paused-callback-${mode}`;
      const api = anApi();
      vi.mocked(api.createArtifactUpload).mockResolvedValue(aSession({ id }));
      vi.mocked(api.getArtifactUpload).mockResolvedValue(aSession({ id }));
      const file = new File(["12345678"], "part.stl");
      const options = {
        api,
        digest,
        onSession: (id: string) => {
          expect(pauseArtifactUpload(id)).toBe(true);
        },
      };
      const pending =
        mode === "upload"
          ? uploadArtifact(file, { purpose: "model", target_role: "new_model" }, options)
          : resumeArtifactUpload(id, file, options);
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(isArtifactUploadActive(id)).toBe(false);
      expect(api.getArtifactUploadPlan).not.toHaveBeenCalled();
    },
  );
});
