/** Storage profiles keep credentials server-side while the browser sends exact wire shapes. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { invalidateApiCache } from "@/lib/api/request";
import {
  createStorageConnection,
  deleteStorageConnection,
  listStorageConnections,
  probeStorageConnection,
  updateStorageConnection,
} from "@/lib/api/storage-connections";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const connection = {
  id: 4,
  name: "TrueNAS MinIO",
  kind: "s3",
  purpose: "both",
  configuration: {
    provider: "s3_self_hosted",
    bucket: "models",
    endpoint_url: "https://minio.example.test",
    root: "library",
  },
  secret_fields_set: ["access_key", "secret_key"],
  enabled: true,
  manual_backup_enabled: true,
  automatic_backup_enabled: true,
};

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listStorageConnections", () => {
  it("reads current profiles without cache", async () => {
    respondWith([connection]);

    const result = await listStorageConnections();

    expect(result).toEqual([connection]);
    expectRequest("/api/v1/storage-connections");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("createStorageConnection", () => {
  it("sends credentials only in the create body", async () => {
    respondWith(connection);
    const body = {
      name: connection.name,
      kind: "s3" as const,
      configuration: connection.configuration,
      secrets: { access_key: "access", secret_key: "secret" },
    };

    await createStorageConnection(body);

    expectRequest("/api/v1/storage-connections", "POST");
    expect(lastBody()).toEqual(body);
  });
});

describe("probeStorageConnection", () => {
  it("probes the saved server-side profile", async () => {
    respondWith({ ok: true });

    await probeStorageConnection(connection.id);

    expectRequest("/api/v1/storage-connections/4/probe", "POST");
    expect(lastBody()).toEqual({});
  });
});

describe("updateStorageConnection", () => {
  it("pauses or resumes one saved profile", async () => {
    respondWith({ ...connection, enabled: false });

    await updateStorageConnection(connection.id, { enabled: false });

    expectRequest("/api/v1/storage-connections/4", "PATCH");
    expect(lastBody()).toEqual({ enabled: false });
  });

  it("changes the workflows allowed to reuse one profile", async () => {
    respondWith({ ...connection, purpose: "library" });

    await updateStorageConnection(connection.id, { purpose: "library" });

    expectRequest("/api/v1/storage-connections/4", "PATCH");
    expect(lastBody()).toEqual({ purpose: "library" });
  });

  it("sends independent backup-selection fields", async () => {
    respondWith({
      ...connection,
      manual_backup_enabled: false,
      automatic_backup_enabled: true,
    });

    await updateStorageConnection(connection.id, {
      manual_backup_enabled: false,
      automatic_backup_enabled: true,
    });

    expectRequest("/api/v1/storage-connections/4", "PATCH");
    expect(lastBody()).toEqual({
      manual_backup_enabled: false,
      automatic_backup_enabled: true,
    });
  });
});

describe("deleteStorageConnection", () => {
  it("deletes only the addressed reusable profile", async () => {
    respondWith(null, 204);

    await expect(deleteStorageConnection(connection.id)).resolves.toBeUndefined();

    expectRequest("/api/v1/storage-connections/4", "DELETE");
  });
});

describe("storage connection caller cancellation", () => {
  it.each(["read", "create", "update", "probe", "delete"] as const)(
    "cancels an active %s request",
    async (operation) => {
      const controller = new AbortController();
      let signal: AbortSignal | null | undefined;
      fetchMock.mockImplementation(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            signal = init?.signal;
            signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
          }),
      );
      const options = { signal: controller.signal };
      const request =
        operation === "read"
          ? listStorageConnections(options)
          : operation === "create"
            ? createStorageConnection(
                {
                  name: "Create",
                  kind: "s3",
                  configuration: { bucket: "printstash" },
                  secrets: { secret_key: "FakeWireStorageSecret" },
                },
                options,
              )
            : operation === "update"
              ? updateStorageConnection(4, { name: "Update" }, options)
              : operation === "probe"
                ? probeStorageConnection(4, options)
                : deleteStorageConnection(4, options);
      controller.abort();
      await expect(request).rejects.toMatchObject({ name: "AbortError" });
      expect(signal?.aborted).toBe(true);
    },
  );
});
