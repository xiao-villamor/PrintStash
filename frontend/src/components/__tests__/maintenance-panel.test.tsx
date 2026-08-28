/*
 * The audit that checks the vault against itself.
 *
 * The database says a file exists; the storage backend is where it actually
 * lives, and the two can drift — a blob deleted out from under the row, a
 * checksum that no longer matches, an external volume that unmounted. Nobody
 * notices until they try to print, which is the worst possible moment. This
 * panel is the only place that difference is visible.
 *
 * Severity is the whole point of the list. A missing artifact is data loss; an
 * unreferenced thumbnail is housekeeping. Presenting them alike buries the one
 * finding worth acting on tonight among forty that can wait, so the filter is
 * load-bearing rather than a convenience.
 *
 * Repair mutates storage, so it stops at a confirmation. "Ignore" does not — it
 * only silences a finding — and conflating the two would put a destructive
 * action one click away.
 *
 * A code this build has no wording for renders as the raw code. A blank label
 * would hide a finding entirely, which is the one outcome an integrity check
 * must never produce.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaintenancePanel } from "@/components/maintenance-panel";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { VaultAuditFinding, VaultAuditRun } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aFinding(over: Partial<VaultAuditFinding> = {}): VaultAuditFinding {
  return {
    id: 11,
    run_id: 1,
    code: "owned_blob_missing",
    severity: "critical",
    resource_type: "file",
    resource_identifier: "files/1/cube.stl",
    repair_action: "requeue",
    state: "open",
    details: {},
    created_at: FROZEN_NOW,
    resolved_at: null,
    resolved_by: null,
    ...over,
  };
}

function anAudit(over: Partial<VaultAuditRun> = {}): VaultAuditRun {
  return {
    id: 1,
    requested_by: 1,
    mode: "quick",
    state: "completed",
    info_count: 0,
    warning_count: 0,
    critical_count: 1,
    progress: 100,
    current_phase: null,
    error_code: null,
    started_at: FROZEN_NOW,
    finished_at: FROZEN_NOW,
    created_at: FROZEN_NOW,
    findings: [aFinding()],
    ...over,
  };
}

const A_BACKUP = {
  backup_id: "2026-01-01T000000Z",
  created_at: FROZEN_NOW,
  location: "local",
  app_version: "0.12.1",
  file_count: 42,
  size_bytes: 1024,
  storage_backend: "local",
};

function renderPanel(options: RenderAppOptions & { audit?: VaultAuditRun | null } = {}) {
  const { audit = anAudit(), routes = {}, ...rest } = options;
  return renderApp(<MaintenancePanel />, {
    routes: {
      "GET /api/v1/maintenance/audits/latest": audit
        ? json(audit)
        : json({ detail: "not_found" }, 404),
      "GET /api/v1/backups": json([]),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MaintenancePanel", () => {
  describe("before anything has been audited", () => {
    it("says so", async () => {
      renderPanel({ audit: null });

      expect(await screen.findByText("No audit has run yet.")).toBeInTheDocument();
    });

    it("offers a quick audit", async () => {
      renderPanel({ audit: null });

      expect(await screen.findByRole("button", { name: "Quick Audit" })).toBeEnabled();
    });

    it("offers a full audit as well", async () => {
      // Quick skips checksum reads, which is the difference between a minute
      // and an hour on a large vault — they are not interchangeable.
      renderPanel({ audit: null });

      expect(await screen.findByRole("button", { name: "Full Audit" })).toBeEnabled();
    });
  });

  describe("starting one", () => {
    it("asks for the mode the operator chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPanel({
        audit: null,
        routes: { "POST /api/v1/maintenance/audits": json(anAudit({ mode: "full" })) },
      });

      await user.click(await screen.findByRole("button", { name: "Full Audit" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          mode: "full",
        }),
      );
    });

    it("will not start a second one while the first is running", async () => {
      // Two audits over the same storage double the read load for no extra
      // information.
      renderPanel({ audit: anAudit({ state: "running", progress: 40 }) });

      expect(await screen.findByRole("button", { name: "Quick Audit" })).toBeDisabled();
    });

    it("offers a way to stop one mid-run", async () => {
      renderPanel({ audit: anAudit({ state: "running", progress: 40 }) });

      expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
    });

    it("reports how far along it is", async () => {
      renderPanel({ audit: anAudit({ state: "running", progress: 40 }) });

      expect(await screen.findByLabelText("40 percent complete")).toBeInTheDocument();
    });
  });

  describe("what the audit found", () => {
    it("names a finding in words rather than codes", async () => {
      renderPanel();

      expect(await screen.findByText("Owned Artifact is missing")).toBeInTheDocument();
    });

    it("falls back to the raw code for a finding this build does not know", async () => {
      // A blank label hides a finding entirely, which is the one outcome an
      // integrity check must never produce.
      renderPanel({ audit: anAudit({ findings: [aFinding({ code: "brand_new_check" })] }) });

      expect(await screen.findByText("brand_new_check")).toBeInTheDocument();
    });

    it("says which resource it is about", async () => {
      renderPanel();

      expect(await screen.findByText(/files\/1\/cube\.stl/)).toBeInTheDocument();
    });

    it("says so when the audit found nothing", async () => {
      renderPanel({ audit: anAudit({ critical_count: 0, findings: [] }) });

      expect(await screen.findByText("No findings in this category.")).toBeInTheDocument();
    });

    it("narrows the list to one severity", async () => {
      // Data loss and housekeeping presented alike buries the one finding worth
      // acting on tonight.
      const user = userEvent.setup();
      renderPanel({
        audit: anAudit({
          warning_count: 1,
          findings: [
            aFinding(),
            aFinding({ id: 12, code: "thumbnail_missing", severity: "warning" }),
          ],
        }),
      });
      await screen.findByText("Owned Artifact is missing");

      await user.click(screen.getByRole("button", { name: "warning 1" }));

      expect(screen.queryByText("Owned Artifact is missing")).toBeNull();
    });
  });

  describe("acting on a finding", () => {
    it("asks before repairing", async () => {
      // A repair mutates storage; there is nothing behind it.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPanel();
      await screen.findByText("Owned Artifact is missing");

      await user.click(screen.getByRole("button", { name: /Repair/ }));

      expect(requestsWithMethod("POST").some((call) => call.url.includes("/repair"))).toBe(false);
    });

    it("repairs once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPanel({
        routes: {
          "POST /api/v1/maintenance/findings/11/repair": json(aFinding({ state: "resolved" })),
          "GET /api/v1/maintenance/audits/1": json(anAudit({ findings: [] })),
        },
      });
      await screen.findByText("Owned Artifact is missing");
      await user.click(screen.getByRole("button", { name: /Repair/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Repair" }),
      );

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.endsWith("/findings/11/repair")),
        ).toBe(true),
      );
    });

    it("ignores a finding without asking", async () => {
      // Ignoring only silences it; nothing on disk changes, so a confirmation
      // would train the operator to click through the one that matters.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPanel({
        routes: {
          "POST /api/v1/maintenance/findings/11/ignore": json(aFinding({ state: "ignored" })),
          "GET /api/v1/maintenance/audits/1": json(anAudit({ findings: [] })),
        },
      });
      await screen.findByText("Owned Artifact is missing");

      await user.click(screen.getByRole("button", { name: /Ignore/ }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.endsWith("/findings/11/ignore")),
        ).toBe(true),
      );
    });

    it("offers no repair for a finding nothing can fix", async () => {
      // An unclaimed storage object has no repair action; a button that 400s
      // reads as the audit being broken.
      renderPanel({ audit: anAudit({ findings: [aFinding({ repair_action: null })] }) });

      await screen.findByText("Owned Artifact is missing");
      expect(screen.queryByRole("button", { name: /Repair/ })).toBeNull();
    });
  });

  describe("verifying a backup", () => {
    it("says a backup has not been checked this session", async () => {
      // Verification reads the whole archive, so it is never done implicitly —
      // and an unverified backup must not read as a verified one.
      renderPanel({ routes: { "GET /api/v1/backups": json([A_BACKUP]) } });

      expect(await screen.findByText("Not verified this session")).toBeInTheDocument();
    });

    it("reports what a good archive contained", async () => {
      const user = userEvent.setup();
      renderPanel({
        routes: {
          "GET /api/v1/backups": json([A_BACKUP]),
          "POST /api/v1/backups/2026-01-01T000000Z/verify": json({
            backup_id: A_BACKUP.backup_id,
            valid: true,
            app_compatible: true,
            manifest_version: "1",
            checked_members: 42,
            findings: [],
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Verify/ }));

      expect(await screen.findByText("42 members verified")).toBeInTheDocument();
    });

    it("reports an archive that did not verify", async () => {
      // A backup nobody can restore is worse than no backup, because it is
      // counted on.
      const user = userEvent.setup();
      renderPanel({
        routes: {
          "GET /api/v1/backups": json([A_BACKUP]),
          "POST /api/v1/backups/2026-01-01T000000Z/verify": json({
            backup_id: A_BACKUP.backup_id,
            valid: false,
            app_compatible: true,
            manifest_version: "1",
            checked_members: 42,
            findings: [{ code: "backup_member_missing", member: "files/1/cube.stl" }],
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Verify/ }));

      expect(await screen.findByText("1 verification findings")).toBeInTheDocument();
    });
  });
});
