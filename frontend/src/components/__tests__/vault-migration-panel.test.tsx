/** Vault migration requires a verified plan and explicit cutover; uncertain recovery never resumes writes implicitly. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { VaultMigrationPanel } from "@/components/vault-migration-panel";
import { aMigrationBackup, aMigrationProvider, aVaultMigration } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import type { VaultMigrationRun } from "@/lib/api/vault-migration";

function setup(runs: VaultMigrationRun[] = [], routes: RouteTable = {}) {
  return renderApp(<VaultMigrationPanel />, {
    routes: {
      "GET /api/v1/storage/migrations/migration-1/report": () =>
        json({ ...aVaultMigration(), ...runs[0], resource_kind_totals: [], recent_failures: [] }),
      "GET /api/v1/storage/migrations/migration-1": () => json(runs[0] ?? aVaultMigration()),
      "GET /api/v1/storage/migrations": () => json(runs),
      "GET /api/v1/storage/providers": () => json([aMigrationProvider()]),
      "GET /api/v1/backups/sources": () => json([aMigrationBackup()]),
      ...routes,
    },
  });
}
describe("VaultMigrationPanel", () => {
  it("requires a backup before preflight", async () => {
    setup();
    expect(await screen.findByRole("button", { name: "Check migration plan" })).toBeDisabled();
    expect(screen.getByText(/verified during preflight/)).toBeVisible();
  });
  it("confirms the final write pause", async () => {
    setup([aVaultMigration({ state: "ready", verified_objects: 10 })]);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Switch Vault storage" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("writes are paused");
  });
  it("requires recovery before resuming a stopped copy", async () => {
    setup([aVaultMigration({ state: "copying", recovery_required: true })]);
    expect(await screen.findByRole("button", { name: "Recover migration" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Resume copy" })).not.toBeInTheDocument();
  });
});

describe("Migration safeguards", () => {
  it("renders unknown remote capacity", async () => {
    setup([aVaultMigration({ capacity_warnings: ["capacity_unknown"] })]);
    expect(await screen.findByText(/provider does not report free capacity/)).toBeVisible();
  });
  it("blocks an expired plan", async () => {
    setup([aVaultMigration({ expires_at: "2020-01-01T00:00:00Z" })]);
    expect(await screen.findByRole("button", { name: "Start verified copy" })).toBeDisabled();
    expect(screen.getByText(/plan has expired/)).toBeVisible();
  });
  it("retains the source throughout its grace period", async () => {
    setup([aVaultMigration({ state: "active", cleanup_after: "2099-01-01T00:00:00Z" })]);
    await userEvent
      .setup()
      .selectOptions(await screen.findByLabelText("Recent backup"), "backup-1:exact-backup-source");
    expect(screen.getByRole("button", { name: "Clean up retained source" })).toBeDisabled();
    expect(screen.getByText(/will not be deleted automatically/)).toBeVisible();
  });
  it("keeps candidate bytes when discard is cancelled", async () => {
    const app = setup([aVaultMigration()]);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Discard copied destination" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("reports retained cleanup findings", async () => {
    const current = aVaultMigration({
      state: "active",
      cleanup_after: "2020-01-01T00:00:00Z",
      full_audit: { id: 1, state: "completed", critical_count: 0, warning_count: 0 },
    });
    setup([current], {
      "POST /api/v1/storage/migrations/migration-1/cleanup": () =>
        json({ ...current, cleanup_findings: [{ object_id: 5, code: "identity_changed" }] }),
    });
    const user = userEvent.setup();
    await user.selectOptions(
      await screen.findByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Clean up retained source" }));
    await user.click(screen.getAllByRole("button", { name: "Clean up retained source" }).at(-1)!);
    expect(await screen.findByText(/Some objects were retained/)).toBeVisible();
  });
  it("rechecks state after an uncertain cutover response", async () => {
    const current = aVaultMigration({ state: "ready", verified_objects: 10 });
    setup([current], {
      "POST /api/v1/storage/migrations/migration-1/cutover": () =>
        json({ detail: "migration_recovery_ambiguous" }, 409),
      "GET /api/v1/storage/migrations/migration-1": () =>
        json({ ...current, state: "activating", recovery_required: true }),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Switch Vault storage" }));
    await user.click(screen.getAllByRole("button", { name: "Switch Vault storage" }).at(-1)!);
    expect(await screen.findByRole("button", { name: "Recover migration" })).toBeVisible();
    expect(screen.queryByText("Destination is active")).not.toBeInTheDocument();
  });
  it("resumes a bounded copy to readiness", async () => {
    setup([aVaultMigration({ state: "paused" })], {
      "POST /api/v1/storage/migrations/migration-1/resume": () =>
        json(aVaultMigration({ state: "ready", verified_objects: 10 })),
    });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Resume copy" }));
    expect(await screen.findByRole("button", { name: "Switch Vault storage" })).toBeVisible();
  });
  it("preflights the selected destination", async () => {
    const app = setup([], {
      "POST /api/v1/storage/migrations/preflight": () => json(aVaultMigration()),
    });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Data directory"), "/new/files");
    await user.type(screen.getByLabelText("Thumbnail directory"), "/new/thumbs");
    await user.selectOptions(
      screen.getByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Check migration plan" }));
    expect(await screen.findByRole("button", { name: "Start verified copy" })).toBeVisible();
    expect(app.requestsWithMethod("POST")[0].body).toBe(
      JSON.stringify({
        destination: { data_dir: "/new/files", thumb_dir: "/new/thumbs", provider: "local" },
        backup_id: "backup-1",
        backup_source_ref: "exact-backup-source",
        policy: { retention_days: 7, concurrency: 1, bandwidth_bytes_per_second: null },
      }),
    );
  });
  it.each([
    { code: "storage_capacity_exceeded", message: "There is not enough available storage" },
    { code: "migration_destination_collision", message: "The destination is not empty" },
    {
      code: "migration_verified_compatible_backup_required",
      message: "Choose a recent, verified, compatible backup",
    },
  ])("explains preflight refusal $code", async ({ code, message }) => {
    setup([], { "POST /api/v1/storage/migrations/preflight": () => json({ detail: code }, 409) });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Data directory"), "/new/files");
    await user.type(screen.getByLabelText("Thumbnail directory"), "/new/thumbs");
    await user.selectOptions(
      screen.getByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Check migration plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });
});

describe("Migration execution", () => {
  it("requests a durable pause", async () => {
    const current = aVaultMigration({ state: "copying" });
    const app = setup([current], {
      "POST /api/v1/storage/migrations/migration-1/pause": () =>
        json({ ...current, state: "paused" }),
    });
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Pause after this batch" }));
    expect(await screen.findByRole("button", { name: "Resume copy" })).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("starts the reviewed plan before copying", async () => {
    const current = aVaultMigration();
    const app = setup([current], {
      "POST /api/v1/storage/migrations/migration-1/start": () =>
        json({ ...current, state: "ready", verified_objects: 10 }),
      "POST /api/v1/storage/migrations/migration-1/advance": () =>
        json({ ...current, state: "ready", verified_objects: 10 }),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start verified copy" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("source-identity");
    expect(screen.getByRole("dialog")).toHaveTextContent("destination-identity");
    expect(screen.getByRole("dialog")).toHaveTextContent("Backup backup-1 was verified");
    await user.click(screen.getAllByRole("button", { name: "Start verified copy" }).at(-1)!);
    expect(await screen.findByRole("button", { name: "Switch Vault storage" })).toBeVisible();
    expect(app.requestsWithMethod("POST")[0].body).toBe(
      JSON.stringify({ plan_digest: current.plan_digest }),
    );
  });
  it("recovers without starting another copy implicitly", async () => {
    const current = aVaultMigration({ state: "paused", recovery_required: true });
    const app = setup([current], {
      "POST /api/v1/storage/migrations/migration-1/recover": () =>
        json({ ...current, recovery_required: false }),
    });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Recover migration" }));
    expect(await screen.findByRole("button", { name: "Resume copy" })).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("shows completed source cleanup", async () => {
    const current = aVaultMigration({
      state: "active",
      cleanup_after: "2020-01-01T00:00:00Z",
      full_audit: { id: 1, state: "completed", critical_count: 0, warning_count: 0 },
    });
    setup([current], {
      "POST /api/v1/storage/migrations/migration-1/cleanup": () =>
        json({ ...current, state: "cleaned", source_retained: false }),
    });
    const user = userEvent.setup();
    await user.selectOptions(
      await screen.findByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Clean up retained source" }));
    await user.click(screen.getAllByRole("button", { name: "Clean up retained source" }).at(-1)!);
    expect(
      await screen.findByText("Retained source cleanup completed", { exact: true }),
    ).toBeVisible();
  });
  it("loads recoverable runs when backup listing fails", async () => {
    setup([aVaultMigration({ state: "activating", recovery_required: true })], {
      "GET /api/v1/backups/sources": () => json({ detail: "maintenance" }, 503),
    });
    expect(await screen.findByRole("button", { name: "Recover migration" })).toBeVisible();
  });
  it("shows a failed status request", async () => {
    setup([], { "GET /api/v1/storage/migrations": () => json({ detail: "unavailable" }, 503) });
    expect(await screen.findByRole("alert")).toHaveTextContent("could not finish");
  });
  it("prevents incomplete destination submission", async () => {
    const app = setup();
    const user = userEvent.setup();
    await user.selectOptions(
      await screen.findByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Check migration plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Complete the required destination fields",
    );
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("refreshes a completed operation after a response was lost", async () => {
    const app = setup([aVaultMigration({ state: "activating" })]);
    await screen.findByText("Final switch in progress", { exact: true });
    app.route({
      "GET /api/v1/storage/migrations": () => json([aVaultMigration({ state: "active" })]),
    });
    await userEvent.setup().click(screen.getByRole("button", { name: "Refresh migration status" }));
    await waitFor(() =>
      expect(screen.getByText("Destination is active", { exact: true })).toBeVisible(),
    );
  });
});

describe("Migration policy and completion evidence", () => {
  it("submits an explicit migration policy", async () => {
    const app = setup([], {
      "POST /api/v1/storage/migrations/preflight": () => json(aVaultMigration()),
    });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Data directory"), "/new/files");
    await user.type(screen.getByLabelText("Thumbnail directory"), "/new/thumbs");
    await user.clear(screen.getByLabelText("Retain source for days"));
    await user.type(screen.getByLabelText("Retain source for days"), "14");
    await user.clear(screen.getByLabelText("Concurrent copies"));
    await user.type(screen.getByLabelText("Concurrent copies"), "3");
    await user.type(screen.getByLabelText(/Bandwidth limit/), "1048576");
    await user.selectOptions(
      screen.getByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Check migration plan" }));
    await screen.findByRole("button", { name: "Start verified copy" });
    expect(JSON.parse(String(app.requestsWithMethod("POST")[0].body))).toMatchObject({
      policy: { retention_days: 14, concurrency: 3, bandwidth_bytes_per_second: 1048576 },
    });
  });
  it("rejects bandwidth below the supported minimum before requesting a plan", async () => {
    const app = setup();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Data directory"), "/new/files");
    await user.type(screen.getByLabelText("Thumbnail directory"), "/new/thumbs");
    await user.type(screen.getByLabelText(/Bandwidth limit/), "100");
    await user.selectOptions(
      screen.getByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Check migration plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 1024 bytes/second");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("shows persisted migration evidence", async () => {
    setup([
      aVaultMigration({
        state: "paused",
        copied_objects: 8,
        copied_bytes: 8192,
        verified_objects: 7,
        verified_bytes: 7168,
        skipped_objects: 2,
        skipped_bytes: 2048,
        failed_objects: 1,
        failed_bytes: 1024,
        delta_objects: 3,
        source_provider_ref: "source-safe-identity",
        destination_provider_ref: "destination-safe-identity",
        capacity_resources: [{ role: "destination", required_bytes: 32768, available_bytes: null }],
        phase_history: [
          { phase: "planned", at: "2026-09-01T10:00:00Z" },
          { phase: "copying", at: "2026-09-01T10:01:00Z" },
        ],
        pre_audit: { id: 4, state: "completed", critical_count: 0, warning_count: 0 },
      }),
    ]);
    expect(await screen.findByText(/source-safe-identity/)).toHaveTextContent(
      "destination-safe-identity",
    );
    expect(screen.getByText("8 objects · 8,192 bytes")).toBeVisible();
    expect(screen.getByText("7 objects · 7,168 bytes")).toBeVisible();
    expect(screen.getByText("2 objects · 2,048 bytes")).toBeVisible();
    expect(screen.getByText("1 objects · 1,024 bytes")).toBeVisible();
    expect(screen.getByText(/destination: 32,768 bytes peak · Not measured/)).toBeVisible();
    expect(screen.getByText(/Preflight Quick audit/)).toBeVisible();
    await userEvent.setup().click(screen.getByText("Phase history"));
    expect(screen.getByText(/Copying and verifying ·/)).toBeVisible();
  });
  it("keeps cleanup disabled without successful Full audit even after grace", async () => {
    setup([
      aVaultMigration({
        state: "active",
        cleanup_after: "2020-01-01T00:00:00Z",
        full_audit: { id: 4, state: "completed", critical_count: 1, warning_count: 0 },
      }),
    ]);
    await userEvent
      .setup()
      .selectOptions(await screen.findByLabelText("Recent backup"), "backup-1:exact-backup-source");
    expect(screen.getByRole("button", { name: "Clean up retained source" })).toBeDisabled();
    expect(screen.getByText(/Source cleanup requires a successful Full audit/)).toBeVisible();
  });
  it("shows the requested Full audit result", async () => {
    const run = aVaultMigration({ state: "active" });
    const app = setup([run], {
      "POST /api/v1/storage/migrations/migration-1/full-audit": () =>
        json({
          ...run,
          full_audit: { id: 4, state: "completed", critical_count: 0, warning_count: 0 },
        }),
    });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Run Full audit" }));
    expect(await screen.findByText("completed · 0 critical · 0 warnings")).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    expect(screen.getByText(/previous source is retained/)).toBeVisible();
  });
  it("retains source indefinitely through an explicit action", async () => {
    const run = aVaultMigration({ state: "active" });
    const app = setup([run], {
      "POST /api/v1/storage/migrations/migration-1/retain": () =>
        json({ ...run, cleanup_outcome: "retained_indefinitely", cleanup_after: null }),
    });
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Retain source indefinitely" }));
    expect(
      await screen.findByText("Source retained indefinitely by operator choice."),
    ).toBeVisible();
    expect(JSON.parse(String(app.requestsWithMethod("POST")[0].body))).toEqual({
      remove_credentials: false,
    });
  });
  it("requires confirmation before removing credentials for manual cleanup", async () => {
    const run = aVaultMigration({ state: "active" });
    const app = setup([run], {
      "POST /api/v1/storage/migrations/migration-1/retain": () =>
        json({ ...run, cleanup_outcome: "manual_cleanup" }),
    });
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Remove retained credentials for manual cleanup" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("Source files are retained");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    await user.click(
      screen
        .getAllByRole("button", { name: "Remove retained credentials for manual cleanup" })
        .at(-1)!,
    );
    expect(
      await screen.findByText("Credentials removed. Source files require manual cleanup."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Clean up retained source" }),
    ).not.toBeInTheDocument();
    expect(JSON.parse(String(app.requestsWithMethod("POST")[0].body))).toEqual({
      remove_credentials: true,
    });
  });
  it("retries a reported recoverable object failure", async () => {
    const run = aVaultMigration({ state: "failed", retryable: true, failed_objects: 1 });
    const app = setup([run], {
      "GET /api/v1/storage/migrations/migration-1/report": () =>
        json({
          ...run,
          resource_kind_totals: [],
          recent_failures: [
            { object_id: 1, code: "migration_destination_collision", retryable: true },
          ],
        }),
      "POST /api/v1/storage/migrations/migration-1/retry": () =>
        json({ ...run, state: "copying", failed_objects: 0 }),
    });
    expect(await screen.findByText("Recent failures")).toBeVisible();
    expect(screen.getByText(/destination is not empty/)).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry failed objects" }));
    expect(await screen.findByText(/durable worker continues/)).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("polls durable worker progress without issuing copy or pause mutations", async () => {
    const current = aVaultMigration({ state: "copying" });
    const app = setup([current], {
      "GET /api/v1/storage/migrations/migration-1": () =>
        json({ ...current, state: "ready", verified_objects: 10 }),
    });
    expect(await screen.findByText(/durable worker continues/)).toBeVisible();
    expect(
      await screen.findByRole("button", { name: "Switch Vault storage" }, { timeout: 4000 }),
    ).toBeVisible();
    app.unmount();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
});

describe("Migration report projection", () => {
  it("shows owned byte totals by resource kind", async () => {
    const run = aVaultMigration();
    setup([run], {
      "GET /api/v1/storage/migrations/migration-1/report": () =>
        json({
          ...run,
          recent_failures: [],
          resource_kind_totals: [{ resource_type: "artifact", objects: 8, bytes: 16384 }],
        }),
    });
    await userEvent.setup().click(await screen.findByText("Owned objects by resource kind"));
    expect(screen.getByText("artifact · 8 objects · 16,384 bytes")).toBeVisible();
  });
  it("permits cleanup after a successful Full audit with noncritical warnings", async () => {
    setup([
      aVaultMigration({
        state: "active",
        cleanup_after: "2020-01-01T00:00:00Z",
        full_audit: { id: 4, state: "completed", critical_count: 0, warning_count: 1 },
      }),
    ]);
    await userEvent
      .setup()
      .selectOptions(await screen.findByLabelText("Recent backup"), "backup-1:exact-backup-source");
    expect(screen.getByRole("button", { name: "Clean up retained source" })).toBeEnabled();
  });
});

describe("Migration recovery navigation", () => {
  it("offers recovery instructions during maintenance", async () => {
    setup([aVaultMigration({ state: "recovery_required", recovery_required: true })]);
    expect(
      await screen.findByRole("link", { name: "Recovery and reverse migration instructions" }),
    ).toHaveAttribute(
      "href",
      "https://github.com/xiao-villamor/PrintStash/blob/main/docs/storage-migration.md",
    );
  });
  it("opens another persisted run from history", async () => {
    setup([aVaultMigration(), aVaultMigration({ id: "older-run", state: "discarded" })]);
    await userEvent
      .setup()
      .selectOptions(await screen.findByLabelText("Migration history"), "older-run");
    expect(screen.getByText("Migration candidate discarded", { selector: "h4" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Start verified copy" })).not.toBeInTheDocument();
  });
  it("prepares a fresh destination after completed cleanup", async () => {
    setup([aVaultMigration({ state: "cleaned", source_retained: false })]);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Prepare another migration" }));
    expect(screen.getByLabelText("Data directory")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Check migration plan" })).toBeDisabled();
  });
  it("discards only the confirmed candidate", async () => {
    const run = aVaultMigration({ state: "paused" });
    const app = setup([run], {
      "POST /api/v1/storage/migrations/migration-1/cleanup": () =>
        json({ ...run, state: "discarded" }),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Discard copied destination" }));
    await user.click(screen.getAllByRole("button", { name: "Discard copied destination" }).at(-1)!);
    expect(
      await screen.findByText("Migration candidate discarded", { selector: "h4" }),
    ).toBeVisible();
    expect(JSON.parse(String(app.requestsWithMethod("POST")[0].body))).toEqual({
      confirmation: run.id,
      source: false,
    });
  });
  it("surfaces report download failure without losing saved progress", async () => {
    setup([aVaultMigration()], {
      "GET /api/v1/storage/migrations/migration-1/report": () =>
        json({ detail: "unavailable" }, 503),
    });
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Download migration report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not finish");
    expect(screen.getByRole("button", { name: "Start verified copy" })).toBeVisible();
  });
});

describe("Migration candidate credentials", () => {
  it("sends fresh candidate credentials without displaying them in the checked plan", async () => {
    const provider = aMigrationProvider({
      id: "s3",
      label: "Object storage",
      fields: [
        {
          name: "bucket",
          label: "Bucket",
          help: "Destination bucket",
          input_type: "text",
          required: true,
          secret: false,
        },
        {
          name: "secret_key",
          label: "Secret key",
          help: "Write-only credential",
          input_type: "password",
          required: true,
          secret: true,
        },
      ],
    });
    const app = setup([], {
      "GET /api/v1/storage/providers": () => json([provider]),
      "POST /api/v1/storage/migrations/preflight": () =>
        json(
          aVaultMigration({
            destination: { provider: "s3", bucket: "candidate-bucket" },
            capacity_warnings: ["capacity_unknown"],
          }),
        ),
    });
    const user = userEvent.setup();
    const secret = await screen.findByLabelText("Secret key");
    expect(secret).toHaveAttribute("type", "password");
    await user.type(screen.getByLabelText("Bucket"), "candidate-bucket");
    await user.type(secret, "migration-test-only-credential");
    await user.selectOptions(
      screen.getByLabelText("Recent backup"),
      "backup-1:exact-backup-source",
    );
    await user.click(screen.getByRole("button", { name: "Check migration plan" }));
    await screen.findByRole("button", { name: "Start verified copy" });
    expect(JSON.parse(String(app.requestsWithMethod("POST")[0].body))).toMatchObject({
      destination: {
        provider: "s3",
        bucket: "candidate-bucket",
        secret_key: "migration-test-only-credential",
      },
    });
    expect(screen.queryByDisplayValue("migration-test-only-credential")).not.toBeInTheDocument();
    expect(screen.queryByText("migration-test-only-credential")).not.toBeInTheDocument();
    expect(screen.getByText("candidate-bucket")).toBeVisible();
  });
  it("blocks destinations unavailable for Vault use", async () => {
    setup([], {
      "GET /api/v1/storage/providers": () =>
        json([aMigrationProvider({ available: false, selectable: false })]),
    });
    await userEvent
      .setup()
      .selectOptions(await screen.findByLabelText("Recent backup"), "backup-1:exact-backup-source");
    expect(screen.getByRole("button", { name: "Check migration plan" })).toBeDisabled();
  });
});
