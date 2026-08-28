/**
 * Vault audits: the operator's view of whether the library on disk still matches
 * the library in the database.
 *
 * The audit reads are uncached, and that is the whole point of them — a running
 * audit's progress is the reason anybody polls it, and a cached answer freezes the
 * progress bar on a run that finished minutes ago.
 *
 * Repair and ignore are different acts on the same finding, so they are different
 * endpoints rather than one endpoint with a mode flag. Collapsing them would make
 * "I looked at this and it is fine" indistinguishable, in the server's log, from
 * "I let the software rewrite it".
 *
 * A backup id is a timestamp, so the verify path has to survive URL encoding.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelVaultAudit,
  getLatestVaultAudit,
  getVaultAudit,
  ignoreAuditFinding,
  repairAuditFinding,
  startVaultAudit,
  verifyBackup,
} from "@/lib/api/maintenance";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startVaultAudit", () => {
  it("starts one in the mode the operator chose", async () => {
    respondWith({ id: 1, mode: "full" });

    await startVaultAudit("full");

    expectRequest("/api/v1/maintenance/audits", "POST");
    expect(lastBody()).toEqual({ mode: "full" });
  });
});

describe("getLatestVaultAudit", () => {
  it("reads the latest run fresh", async () => {
    respondWith({ id: 1 });

    await getLatestVaultAudit();

    // A running audit's progress is the whole reason to poll it.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("getVaultAudit", () => {
  it("reads one run fresh", async () => {
    respondWith({ id: 1 });

    await getVaultAudit(1);

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("cancelVaultAudit", () => {
  it("cancels through the run's own sub-resource", async () => {
    respondWith({ id: 1 });

    await cancelVaultAudit(1);

    expectRequest("/api/v1/maintenance/audits/1/cancel", "POST");
  });
});

describe("repairAuditFinding", () => {
  it("repairs a finding through its own endpoint", async () => {
    respondWith({ id: 5 });

    await repairAuditFinding(5);

    expectRequest("/api/v1/maintenance/findings/5/repair", "POST");
  });
});

describe("ignoreAuditFinding", () => {
  it("ignores a finding through a different endpoint", async () => {
    respondWith({ id: 5 });

    await ignoreAuditFinding(5);

    // Repair and ignore are different acts on the same finding, so they are
    // different endpoints rather than one with a mode flag.
    expectRequest("/api/v1/maintenance/findings/5/ignore", "POST");
  });
});

describe("verifyBackup", () => {
  it("verifies one by its id", async () => {
    respondWith({ valid: true });

    await verifyBackup("2026-01-01T00:00:00Z");

    expectRequest("/api/v1/backups/2026-01-01T00%3A00%3A00Z/verify", "POST");
  });
});
