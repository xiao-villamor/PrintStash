/** Canonical upload creation carries a stable retry identity without provider state. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { createArtifactUpload } from "@/lib/api/artifact-uploads";

afterEach(() => vi.unstubAllGlobals());

describe("artifact upload API", () => {
  it("binds creation retries to the declared representation hash", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "session-1",
          purpose: "model",
          target_role: "new_model",
          target_id: null,
          filename: "part.stl",
          media_type: "model/stl",
          size_bytes: 4,
          state: "created",
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
        }),
        { headers: { "content-type": "application/json" } },
      ),
    );
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
});
