/** Storage profiles keep credentials server-side while the browser sends exact wire shapes. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { invalidateApiCache } from "@/lib/api/request";
import {
  createStorageConnection,
  deleteStorageConnection,
  listStorageConnections,
  probeStorageConnection,
} from "@/lib/api/storage-connections";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const connection = {
  id: 4,
  name: "TrueNAS MinIO",
  kind: "s3",
  configuration: {
    provider: "s3_self_hosted",
    bucket: "models",
    endpoint_url: "https://minio.example.test",
    root: "library",
  },
  secret_fields_set: ["access_key", "secret_key"],
  enabled: true,
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

describe("deleteStorageConnection", () => {
  it("deletes only the addressed reusable profile", async () => {
    respondWith(null, 204);

    await expect(deleteStorageConnection(connection.id)).resolves.toBeUndefined();

    expectRequest("/api/v1/storage-connections/4", "DELETE");
  });
});
