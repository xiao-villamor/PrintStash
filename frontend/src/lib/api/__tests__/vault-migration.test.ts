/** Migration requests preserve the exact run, plan digest, backup source and destructive cleanup choice. */
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  downloadVaultMigrationReport,
  pauseVaultMigration,
  resumeVaultMigration,
  retryVaultMigration,
  auditVaultMigration,
  retainVaultMigration,
  getVaultMigrationReport,
  advanceVaultMigration,
  cleanupVaultMigration,
  cutoverVaultMigration,
  getVaultMigration,
  listVaultMigrations,
  preflightVaultMigration,
  recoverVaultMigration,
  startVaultMigration,
} from "@/lib/api/vault-migration";
import { invalidateApiCache } from "@/lib/api/request";
import { aVaultMigration } from "@/test-support/factories";
import { fetchMock, respondWith, expectRequest, lastBody } from "./_wire";

describe("Vault migration API", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    invalidateApiCache();
    respondWith({
      ...aVaultMigration(),
      policy: { ...aVaultMigration().policy },
      pre_audit: null,
      post_audit: null,
      full_audit: null,
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
  it("reads current migration history without cached progress", async () => {
    respondWith([
      {
        ...aVaultMigration(),
        policy: { ...aVaultMigration().policy },
        pre_audit: null,
        post_audit: null,
        full_audit: null,
      },
    ]);
    await listVaultMigrations();
    await listVaultMigrations();
    expectRequest("/api/v1/storage/migrations");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("encodes the run identity", async () => {
    await getVaultMigration("run/one");
    expectRequest("/api/v1/storage/migrations/run%2Fone");
  });
  it("submits the exact backup source for preflight", async () => {
    const body = {
      destination: { provider: "local", data_dir: "/new/data", thumb_dir: "/new/thumbs" },
      backup_id: "backup-one",
      backup_source_ref: "exact-source",
    };
    await preflightVaultMigration(body);
    expectRequest("/api/v1/storage/migrations/preflight", "POST");
    expect(lastBody()).toEqual(body);
  });
  it("starts the exact reviewed plan", async () => {
    await startVaultMigration("run/one", "reviewed-digest");
    expectRequest("/api/v1/storage/migrations/run%2Fone/start", "POST");
    expect(lastBody()).toEqual({ plan_digest: "reviewed-digest" });
  });
  it.each([
    { action: "advance", invoke: advanceVaultMigration },
    { action: "cutover", invoke: cutoverVaultMigration },
    { action: "recover", invoke: recoverVaultMigration },
    { action: "pause", invoke: pauseVaultMigration },
    { action: "resume", invoke: resumeVaultMigration },
    { action: "retry", invoke: retryVaultMigration },
    { action: "full-audit", invoke: auditVaultMigration },
  ])("requests $action explicitly", async ({ action, invoke }) => {
    await invoke("run/one");
    expectRequest(`/api/v1/storage/migrations/run%2Fone/${action}`, "POST");
    expect(lastBody()).toEqual({});
  });
  it("selects retained-source cleanup with a fresh backup", async () => {
    await cleanupVaultMigration("run-one", true, {
      backup_id: "fresh",
      backup_source_ref: "fresh-source",
    });
    expect(lastBody()).toEqual({
      confirmation: "run-one",
      source: true,
      backup_id: "fresh",
      backup_source_ref: "fresh-source",
    });
  });
  it("selects candidate discard without authorizing source cleanup", async () => {
    await cleanupVaultMigration("run-one", false);
    expect(lastBody()).toEqual({ confirmation: "run-one", source: false });
  });
  it("retains source bytes indefinitely", async () => {
    await retainVaultMigration("run-one");
    expectRequest("/api/v1/storage/migrations/run-one/retain", "POST");
    expect(lastBody()).toEqual({ remove_credentials: false });
  });
  it("requires an explicit request to remove retained credentials", async () => {
    await retainVaultMigration("run-one", true);
    expect(lastBody()).toEqual({ remove_credentials: true });
  });
  it("reads the safe migration report", async () => {
    await getVaultMigrationReport("run/one");
    expectRequest("/api/v1/storage/migrations/run%2Fone/report");
  });
  it("downloads a safe report as a standalone JSON artifact", async () => {
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:migration-report");
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    let filename = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        filename = this.download;
      },
    );
    await downloadVaultMigrationReport("run/one");
    expect(filename).toBe("printstash-migration-run%2Fone.json");
    expect(create.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(document.querySelector("a[download]")).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:migration-report");
  });
  it("retains capacity denial details", async () => {
    respondWith({ detail: "storage_capacity_exceeded" }, 507);
    await expect(
      preflightVaultMigration({ destination: { provider: "local" }, backup_id: "backup-one" }),
    ).rejects.toMatchObject({ status: 507, code: "storage_capacity_exceeded" });
  });
});
