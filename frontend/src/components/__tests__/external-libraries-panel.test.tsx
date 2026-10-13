/*
 * Mirroring a folder that PrintStash does not own.
 *
 * Everything here is qualified by one fact: the files live somewhere else — a
 * mounted folder or remote namespace — and PrintStash only indexes them in place. So the
 * feature is off until an operator turns it on, and removing a source must say
 * out loud that the files were not touched. A "Remove" that reads like a delete
 * is how somebody loses a NAS they spent a weekend organising.
 *
 * Real-time watching is a property of the filesystem, not a preference. A local
 * folder delivers file events; an NFS/SMB mount does not, and asking for events
 * there would leave the source looking watched while it silently went stale. The
 * panel has to report which of the two a mounted source actually got.
 *
 * A scan is long-running, so the button that starts one only tells the truth
 * once the job terminates. Reporting "Scan complete" on the 202 would call a
 * scan that failed a success.
 *
 * A partial scan is the case a green tick would hide: the run finished, and some
 * files still failed to index.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ExternalLibrariesPanel,
  type ExternalLibrariesApi,
} from "@/components/external-libraries-panel";
import { ApiError } from "@/lib/errors";
import { aJob as aSharedJob, aStorageConnection } from "@/test-support/factories";
import { renderApp, json, memberSession } from "@/test-support/render";
import * as taskCenter from "@/lib/task-center";
import { clearLogin } from "@/lib/auth-store";
import { aVaultConfig } from "@/test-support/factories";
import type {
  ExternalLibrary,
  ExternalLibraryCreate,
  ExternalLibraryScanSummary,
  ExternalLibraryUpdate,
  JobStatus,
  JobAccepted,
  StorageConnection,
} from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aSummary(over: Partial<ExternalLibraryScanSummary> = {}): ExternalLibraryScanSummary {
  return {
    added: 3,
    updated: 1,
    removed: 0,
    skipped: 0,
    errors: [],
    error: null,
    aborted: false,
    ...over,
  };
}

function aVolume(over: Partial<ExternalLibrary> = {}): ExternalLibrary {
  return {
    edit_epoch: "a".repeat(32),
    edit_version: 1,
    id: 7,
    name: "NAS models",
    root_path: "/mnt/nas/3d",
    enabled: true,
    scan_interval_minutes: 60,
    scan_schedule: "0 * * * *",
    watch_mode: "auto",
    fs_kind: "local",
    watch_active: true,
    binding_state: "bound",
    binding_reason: null,
    root_enrollable: false,
    collection_mode: "mirror",
    target_collection_id: null,
    last_scanned_at: FROZEN_NOW,
    last_scan_status: "ok",
    last_scan_summary: aSummary(),
    ...over,
  };
}

function aJob(over: Partial<JobStatus> = {}): JobStatus {
  return aSharedJob({
    job_id: "job-1",
    kind: "sources.scan",
    model_id: null,
    file_id: null,
    ...over,
  });
}

/**
 * The panel declares its API as a port precisely so a test can drive it without
 * a fetch layer in between. Each stub is overridable per test.
 */
function stubApi(over: Partial<ExternalLibrariesApi> = {}): ExternalLibrariesApi {
  return {
    getConfig: vi
      .fn<ExternalLibrariesApi["getConfig"]>()
      .mockResolvedValue(aVaultConfig({ external_libraries_enabled: true })),
    updateConfig: vi
      .fn<ExternalLibrariesApi["updateConfig"]>()
      .mockImplementation(async (payload, options) =>
        aVaultConfig({
          external_libraries_enabled: payload.external_libraries_enabled ?? true,
          edit_version: (options?.base?.edit_version ?? 1) + 1,
        }),
      ),
    list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([aVolume()]),
    enroll: vi
      .fn<(id: number, body: { confirm_root_path: string }) => Promise<ExternalLibrary>>()
      .mockResolvedValue(aVolume()),
    create: vi
      .fn<(body: ExternalLibraryCreate) => Promise<ExternalLibrary>>()
      .mockResolvedValue(aVolume()),
    update: vi
      .fn<(id: number, body: ExternalLibraryUpdate) => Promise<ExternalLibrary>>()
      .mockResolvedValue(aVolume()),
    remove: vi.fn<(id: number) => Promise<void>>().mockResolvedValue(undefined),
    scan: vi
      .fn<(id: number) => Promise<JobAccepted>>()
      .mockResolvedValue({ job_id: "job-1", state: "queued", message: "queued" }),
    listConnections: vi.fn<ExternalLibrariesApi["listConnections"]>().mockResolvedValue([]),
    ...over,
  };
}

function renderPanel(
  over: Partial<ExternalLibrariesApi> & { jobStatus?: (id: string) => Promise<JobStatus> } = {},
  canEdit = true,
) {
  const { jobStatus, ...ports } = over;
  const api = stubApi(ports);
  const result = renderApp(<ExternalLibrariesPanel canEdit={canEdit} api={api} />, {
    routes: {
      "GET /api/v1/jobs": async () => json([await (jobStatus ?? (async () => aJob()))("job-1")]),
    },
  });
  return { ...result, api };
}

beforeEach(() => {
  window.localStorage.clear();
  taskCenter.resetTasksForNewSetup();
});

afterEach(() => {
  vi.restoreAllMocks();
  taskCenter.resetTasksForNewSetup();
  vi.unstubAllGlobals();
});

describe("ExternalLibrariesPanel", () => {
  describe("turning the feature on", () => {
    it("shows nothing until it knows whether the feature is on", () => {
      // Rendering the "off" state first and correcting it makes an enabled
      // vault flicker through a screen saying it has no library sources.
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockReturnValue(new Promise(() => {})),
      });

      expect(screen.queryByRole("switch")).toBeNull();
    });

    it("reads as off when the vault has it disabled", async () => {
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValue(aVaultConfig({ external_libraries_enabled: false })),
      });

      const toggle = await screen.findByRole("switch");
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("presents sources as externally owned indexes", async () => {
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValue(aVaultConfig({ external_libraries_enabled: false })),
      });

      expect(await screen.findByText("Library sources")).toBeInTheDocument();
      expect(
        await screen.findByText(/without copying them into Vault storage/),
      ).toBeInTheDocument();
      expect(screen.getByText(/never deleted by PrintStash/)).toBeInTheDocument();
    });

    it("lists nothing while the feature is off", async () => {
      // Volumes are only fetched once the feature is on, so a disabled vault
      // never asks the server about folders it will not mirror.
      const { api } = renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValue(aVaultConfig({ external_libraries_enabled: false })),
      });

      await screen.findByRole("switch");
      expect(api.list).not.toHaveBeenCalled();
    });

    it("sends the observed configuration base when enabling sources", async () => {
      const { api } = renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValue(aVaultConfig({ edit_version: 4, external_libraries_enabled: false })),
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Library sources enabled" }));
      await waitFor(() =>
        expect(api.updateConfig).toHaveBeenCalledWith(
          { external_libraries_enabled: true },
          expect.objectContaining({
            base: { edit_epoch: aVaultConfig().edit_epoch, edit_version: 4 },
          }),
        ),
      );
    });
    it.each([412, 503])("%s: reviews a rejected source toggle before retrying", async (status) => {
      const updateConfig = vi
        .fn<ExternalLibrariesApi["updateConfig"]>()
        .mockRejectedValueOnce(new Error(`HTTP ${status}: {"detail":"edit_conflict"}`))
        .mockResolvedValue(aVaultConfig({ edit_version: 3, external_libraries_enabled: true }));
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValueOnce(aVaultConfig({ external_libraries_enabled: false }))
          .mockResolvedValue(aVaultConfig({ edit_version: 2, external_libraries_enabled: false })),
        updateConfig,
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Library sources enabled" }));
      const review = await screen.findByRole("button", { name: "Review latest version" });
      expect(screen.getByRole("switch", { name: "Library sources enabled" })).toBeDisabled();
      expect(updateConfig).toHaveBeenCalledTimes(1);
      await userEvent.click(review);
      await userEvent.click(
        await screen.findByRole("button", { name: "Save my draft against this version" }),
      );
      await waitFor(() =>
        expect(screen.getByRole("switch", { name: "Library sources enabled" })).toHaveAttribute(
          "aria-checked",
          "true",
        ),
      );
      expect(updateConfig).toHaveBeenLastCalledWith(
        { external_libraries_enabled: true },
        expect.objectContaining({
          base: { edit_epoch: aVaultConfig().edit_epoch, edit_version: 2 },
        }),
      );
    });
    it("retains a blocked source toggle when review fails", async () => {
      const updateConfig = vi
        .fn<ExternalLibrariesApi["updateConfig"]>()
        .mockRejectedValue(new Error('HTTP 412: {"detail":"edit_conflict"}'));
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValueOnce(aVaultConfig({ external_libraries_enabled: false }))
          .mockRejectedValue(new Error('HTTP 503: {"detail":"unavailable"}')),
        updateConfig,
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Library sources enabled" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByText("Library source settings could not be loaded.");
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
      expect(screen.getByRole("switch", { name: "Library sources enabled" })).toBeDisabled();
      expect(updateConfig).toHaveBeenCalledTimes(1);
    });
    it("adopts the reviewed source setting without another write", async () => {
      const updateConfig = vi
        .fn<ExternalLibrariesApi["updateConfig"]>()
        .mockRejectedValue(new Error('HTTP 412: {"detail":"edit_conflict"}'));
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValueOnce(aVaultConfig({ external_libraries_enabled: false }))
          .mockResolvedValue(aVaultConfig({ edit_version: 2, external_libraries_enabled: true })),
        updateConfig,
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Library sources enabled" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await userEvent.click(await screen.findByRole("button", { name: "Use latest version" }));
      expect(screen.getByRole("switch", { name: "Library sources enabled" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
      expect(screen.getByRole("switch", { name: "Library sources enabled" })).toBeEnabled();
      expect(updateConfig).toHaveBeenCalledTimes(1);
    });
    it("hides source review after access is denied", async () => {
      const updateConfig = vi
        .fn<ExternalLibrariesApi["updateConfig"]>()
        .mockRejectedValue(new Error('HTTP 412: {"detail":"edit_conflict"}'));
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValueOnce(aVaultConfig({ external_libraries_enabled: false }))
          .mockRejectedValue(new Error('HTTP 403: {"detail":"forbidden"}')),
        updateConfig,
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Library sources enabled" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByText("Library source settings could not be loaded.");
      expect(
        screen.queryByRole("switch", { name: "Library sources enabled" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
      expect(updateConfig).toHaveBeenCalledTimes(1);
    });
    it("retires a pending source review on logout", async () => {
      const pending = Promise.withResolvers<ReturnType<typeof aVaultConfig>>();
      const updateConfig = vi
        .fn<ExternalLibrariesApi["updateConfig"]>()
        .mockRejectedValue(new Error('HTTP 412: {"detail":"edit_conflict"}'));
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValueOnce(aVaultConfig({ external_libraries_enabled: false }))
          .mockReturnValue(pending.promise),
        updateConfig,
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Library sources enabled" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await act(async () => {
        clearLogin();
        pending.resolve(aVaultConfig({ edit_version: 2, external_libraries_enabled: true }));
      });
      expect(
        screen.queryByRole("region", { name: "Latest saved version" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
      expect(updateConfig).toHaveBeenCalledTimes(1);
    });
    it("enables the feature when the operator turns it on", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValue(aVaultConfig({ external_libraries_enabled: false })),
      });

      await user.click(await screen.findByRole("switch"));

      await waitFor(() =>
        expect(api.updateConfig).toHaveBeenCalledWith(
          { external_libraries_enabled: true },
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("puts the switch back when the server refuses", async () => {
      // A switch left "on" over a feature the server never enabled is a lie the
      // operator only discovers when nothing scans.
      const user = userEvent.setup();
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockResolvedValue(aVaultConfig({ external_libraries_enabled: false })),
        updateConfig: vi
          .fn<ExternalLibrariesApi["updateConfig"]>()
          .mockRejectedValue(new Error("nope")),
      });

      await user.click(await screen.findByRole("switch"));

      await waitFor(() =>
        expect(screen.getAllByRole("switch")[0]).toHaveAttribute("aria-checked", "false"),
      );
    });
  });

  describe("the sources already indexed", () => {
    it("names each mirrored folder", async () => {
      renderPanel();

      expect(await screen.findByText("NAS models")).toBeInTheDocument();
    });

    it("shows the path being indexed in place", async () => {
      renderPanel();

      expect(await screen.findByText("/mnt/nas/3d")).toBeInTheDocument();
    });

    it("explains how to add the first source", async () => {
      renderPanel({ list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([]) });

      expect(await screen.findByText("No library sources yet")).toBeInTheDocument();
      expect(screen.getByText(/mounted folder or connect remote storage/)).toBeInTheDocument();
    });

    it("marks a paused volume", async () => {
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ enabled: false })]),
      });

      expect(await screen.findByText("Paused")).toBeInTheDocument();
    });

    it("explains that a legacy root needs proof before recovery", async () => {
      renderPanel({
        list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([
          aVolume({
            binding_state: "unbound",
            binding_reason: "legacy_library_requires_explicit_enrollment",
            root_enrollable: true,
            watch_active: false,
          }),
        ]),
      });

      expect(await screen.findByText("Needs enrollment")).toBeInTheDocument();
      expect(
        await screen.findByText(/Scans, watching, and writeback stay paused/),
      ).toBeInTheDocument();
      expect(await screen.findByRole("button", { name: "Review and enroll" })).toBeVisible();
    });

    it("shows a missing root as recovery without offering unsafe controls", async () => {
      renderPanel({
        list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([
          aVolume({
            binding_state: "missing",
            binding_reason: "root_path_missing",
            root_enrollable: true,
            watch_active: false,
          }),
        ]),
      });

      expect(await screen.findByText("Root proof unavailable")).toBeInTheDocument();
      expect(await screen.findByRole("button", { name: /Scan now/ })).toBeDisabled();
      expect(await screen.findByRole("button", { name: "Review and enroll" })).toBeVisible();
    });

    it("does not offer enrollment for a conflicting root marker", async () => {
      renderPanel({
        list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([
          aVolume({
            binding_state: "mismatch",
            binding_reason: "root_marker_conflict",
            root_enrollable: false,
            watch_active: false,
          }),
        ]),
      });

      expect(await screen.findByText("Root binding blocked")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Review and enroll" })).toBeNull();
      expect(await screen.findByRole("button", { name: /Scan now/ })).toBeDisabled();
    });

    it("disables the watcher switch until the root is proven", async () => {
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([
            aVolume({ binding_state: "unreadable", root_enrollable: false, watch_active: false }),
          ]),
      });

      expect(await screen.findByRole("switch", { name: "Auto-scan enabled" })).toBeDisabled();
    });
  });

  describe("root enrollment", () => {
    it("confirms the exact displayed root path before enrolling", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([
            aVolume({ binding_state: "unbound", root_enrollable: true, watch_active: false }),
          ]),
      });
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: "Review and enroll" }));

      expect(
        await screen.findByText(/exact mounted path belongs to this PrintStash installation/),
      ).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Enroll root" }));

      await waitFor(() =>
        expect(api.enroll).toHaveBeenCalledWith(
          7,
          { confirm_root_path: "/mnt/nas/3d" },
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("reports successful enrollment with rescan guidance", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ binding_state: "unbound", root_enrollable: true })]),
      });
      await screen.findByRole("button", { name: "Review and enroll" });

      await user.click(screen.getByRole("button", { name: "Review and enroll" }));
      await user.click(await screen.findByRole("button", { name: "Enroll root" }));

      expect(await screen.findByText("Root verified. Rescan to resume indexing.")).toBeVisible();
      expect(api.list).toHaveBeenCalledTimes(1);
    });

    it("surfaces an enrollment refusal", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ binding_state: "missing", root_enrollable: true })]),
        enroll: vi
          .fn<(id: number, body: { confirm_root_path: string }) => Promise<ExternalLibrary>>()
          .mockRejectedValue(new Error("root_marker_conflict")),
      });
      await screen.findByRole("button", { name: "Review and enroll" });

      await user.click(screen.getByRole("button", { name: "Review and enroll" }));
      await user.click(await screen.findByRole("button", { name: "Enroll root" }));

      expect(
        await screen.findByText(
          "The library root belongs to another installation. Verify its location before enrolling it.",
        ),
      ).toBeVisible();
      expect(api.list).toHaveBeenCalledTimes(1);
    });
  });

  describe("how a volume is kept up to date", () => {
    it("says a local folder is watched in real time", async () => {
      renderPanel();

      expect(await screen.findByText("Watching (real-time)")).toBeInTheDocument();
    });

    it("says a network folder falls back to the schedule", async () => {
      // NFS/SMB deliver no file events; a volume that looked watched here would
      // go stale with nothing to show for it.
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ fs_kind: "network", watch_active: false })]),
      });

      expect(await screen.findByText("Network folder — scheduled scans only")).toBeInTheDocument();
    });

    it("says so when watching was turned off deliberately", async () => {
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ watch_mode: "off", watch_active: false })]),
      });

      expect(await screen.findByText("Watching off — scheduled scans only")).toBeInTheDocument();
    });

    it("describes a preset schedule in words", async () => {
      renderPanel();

      expect(await screen.findByText(/· Hourly ·/)).toBeInTheDocument();
    });

    it("shows a custom cron verbatim", async () => {
      // A cron nobody can read as a preset has to be shown as itself, or the
      // operator cannot tell what they configured.
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ scan_schedule: "*/13 * * * *" })]),
      });

      expect(await screen.findByText(/Custom \(\*\/13 \* \* \* \*\)/)).toBeInTheDocument();
    });

    it("saves a new schedule for the volume", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");

      await user.selectOptions(screen.getAllByRole("combobox")[0], "0 0 * * *");

      await waitFor(() =>
        expect(api.update).toHaveBeenCalledWith(
          7,
          { scan_schedule: "0 0 * * *" },
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("pauses a volume without removing it", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("switch", { name: "Auto-scan enabled" }));

      await waitFor(() =>
        expect(api.update).toHaveBeenCalledWith(
          7,
          { enabled: false },
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });
  });

  describe("what the last scan found", () => {
    it("reports what changed", async () => {
      renderPanel();

      expect(await screen.findByText(/\+3 added · 1 updated · 0 removed/)).toBeInTheDocument();
    });

    it("warns when a finished scan still failed on some files", async () => {
      // "partial" is terminal like "ok"; without this the green count hides
      // every file that never made it in.
      renderPanel({
        list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([
          aVolume({
            last_scan_status: "partial",
            last_scan_summary: aSummary({ errors: ["bad.stl"] }),
          }),
        ]),
      });

      expect(await screen.findByText("Some files could not be indexed")).toBeInTheDocument();
    });

    it("surfaces the reason a scan failed outright", async () => {
      renderPanel({
        list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([
          aVolume({
            last_scan_status: "error",
            last_scan_summary: aSummary({ error: "Permission denied" }),
          }),
        ]),
      });

      expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    });

    it("says a volume has never been scanned rather than showing an epoch", async () => {
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ last_scanned_at: null })]),
      });

      expect(await screen.findByText(/last scan Never/)).toBeInTheDocument();
    });
  });

  describe("scanning on demand", () => {
    it("asks the server to scan the chosen volume", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: /Scan now/ }));

      await waitFor(() =>
        expect(api.scan).toHaveBeenCalledWith(
          7,
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("reports success only once the job has terminated", async () => {
      // The 202 means "queued", not "done"; reporting on it would call a failed
      // scan a success.
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: /Scan now/ }));

      expect(await screen.findByText(/Scan complete for "NAS models"/)).toBeInTheDocument();
    });

    it("surfaces a scan that failed", async () => {
      const user = userEvent.setup();
      renderPanel({
        jobStatus: vi
          .fn<(id: string) => Promise<JobStatus>>()
          .mockResolvedValue(aJob({ state: "failed", error: "root_path_missing" })),
      });
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: /Scan now/ }));

      expect(
        await screen.findByText("The library folder is unavailable. Check its path and mount."),
      ).toBeInTheDocument();
    });

    it("re-reads the volume after a failed scan", async () => {
      // The failure itself is state the server recorded, so the row has to catch
      // up rather than keep showing the status from before the attempt.
      const user = userEvent.setup();
      const { api } = renderPanel({
        jobStatus: vi
          .fn<(id: string) => Promise<JobStatus>>()
          .mockResolvedValue(aJob({ state: "failed", error: "root_path_missing" })),
      });
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: /Scan now/ }));

      await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
    });
  });

  describe("adding a library source", () => {
    it("offers every source type before a connection exists", async () => {
      renderPanel({
        listConnections: vi.fn<() => Promise<StorageConnection[]>>().mockResolvedValue([]),
      });

      const sourceType = await screen.findByLabelText("Library source type");
      expect(sourceType).toHaveTextContent("Mounted folder (SMB/NFS/local)");
      expect(sourceType).toHaveTextContent("S3 / compatible");
      expect(sourceType).toHaveTextContent("WebDAV / Nextcloud");
      expect(sourceType).toHaveTextContent("SFTP");
      expect(sourceType).toHaveTextContent("Google Drive");
    });

    it("directs remote connection setup to its settings section", async () => {
      renderPanel({
        listConnections: vi.fn<() => Promise<StorageConnection[]>>().mockResolvedValue([]),
      });

      expect(await screen.findByText(/managed in Settings → Remote storage/)).toBeInTheDocument();
    });

    it("refuses a folder with no name", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Mounted folder path"), "/mnt/x");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      expect(api.create).not.toHaveBeenCalled();
    });

    it("refuses a name with no folder", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "NAS");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      expect(api.create).not.toHaveBeenCalled();
    });

    it("adds the folder the operator described", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "Attic NAS");
      await user.type(screen.getByLabelText("Mounted folder path"), "/mnt/attic");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      await waitFor(() =>
        expect(api.create).toHaveBeenCalledWith(
          {
            name: "Attic NAS",
            root_path: "/mnt/attic",
            scan_schedule: "0 * * * *",
            watch_mode: "auto",
            collection_mode: "mirror",
          },
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("explains the writable mount requirement after enrollment fails", async () => {
      const user = userEvent.setup();
      renderPanel({
        create: vi
          .fn<(body: ExternalLibraryCreate) => Promise<ExternalLibrary>>()
          .mockRejectedValue(new ApiError(409, "root_marker_unwritable", "")),
      });
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "Attic NAS");
      await user.type(screen.getByLabelText("Mounted folder path"), "/mnt/attic");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      expect(await screen.findByText(/must be writable during enrollment/i)).toBeVisible();
    });

    it("adds a read-only remote S3 source through a reusable profile", async () => {
      const user = userEvent.setup();
      const profile = aStorageConnection({
        id: 41,
        name: "TrueNAS MinIO",
        purpose: "both",
        configuration: { bucket: "models" },
      });
      const { api } = renderPanel({
        listConnections: vi.fn<() => Promise<StorageConnection[]>>().mockResolvedValue([profile]),
      });
      await screen.findByText("NAS models");
      await user.selectOptions(await screen.findByLabelText("Library source type"), "s3");
      await user.type(screen.getByLabelText("Source name"), "Remote catalogue");
      await user.selectOptions(screen.getByLabelText("Remote source connection"), "41");
      await user.type(screen.getByLabelText("Source path within connection"), "production");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      await waitFor(() =>
        expect(api.create).toHaveBeenCalledWith(
          {
            name: "Remote catalogue",
            root_path: undefined,
            scan_schedule: "0 * * * *",
            watch_mode: "off",
            collection_mode: "mirror",
            source_kind: "s3",
            connection_id: 41,
            source_prefix: "production",
          },
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("trims a path the operator pasted with whitespace", async () => {
      // A trailing space in a root path is a folder that does not exist, and the
      // failure surfaces much later as an empty scan.
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "  Attic  ");
      await user.type(screen.getByLabelText("Mounted folder path"), "  /mnt/attic  ");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      await waitFor(() =>
        expect(api.create).toHaveBeenCalledWith(
          expect.objectContaining({ name: "Attic", root_path: "/mnt/attic" }),
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("carries the collection layout the operator chose", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "Attic");
      await user.type(screen.getByLabelText("Mounted folder path"), "/mnt/attic");
      await user.selectOptions(screen.getByLabelText("Collection layout"), "single");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      await waitFor(() =>
        expect(api.create).toHaveBeenCalledWith(
          expect.objectContaining({ collection_mode: "single" }),
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("lets the operator write a cron the presets do not cover", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "Attic");
      await user.type(screen.getByLabelText("Mounted folder path"), "/mnt/attic");
      await user.selectOptions(screen.getByLabelText("Scan schedule"), "__custom__");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      await waitFor(() =>
        expect(api.create).toHaveBeenCalledWith(
          expect.objectContaining({ scan_schedule: "0 */2 * * *" }),
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });

    it("surfaces a folder the server rejected", async () => {
      const user = userEvent.setup();
      renderPanel({
        create: vi
          .fn<(body: ExternalLibraryCreate) => Promise<ExternalLibrary>>()
          .mockRejectedValue(new Error("path_not_allowed")),
      });
      await screen.findByText("NAS models");
      await user.type(screen.getByLabelText("Source name"), "Attic");
      await user.type(screen.getByLabelText("Mounted folder path"), "/etc");

      await user.click(screen.getByRole("button", { name: /Add source/ }));

      expect(await screen.findByText("Path not allowed.")).toBeInTheDocument();
    });
  });

  describe("removing a library source", () => {
    it("asks before removing", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: "Remove library source" }));

      expect(api.remove).not.toHaveBeenCalled();
    });

    it("promises the source files are left alone", async () => {
      // Without this the operator has to guess whether "Remove" deletes a NAS.
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: "Remove library source" }));

      expect(
        await screen.findByText(
          /Source files remain untouched in their mounted folder or remote storage/,
        ),
      ).toBeInTheDocument();
    });

    it("removes the volume once confirmed", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.click(screen.getByRole("button", { name: "Remove library source" }));

      await user.click(await screen.findByRole("button", { name: "Remove" }));

      await waitFor(() =>
        expect(api.remove).toHaveBeenCalledWith(
          7,
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ),
      );
    });
  });

  describe("read ownership and command lifetimes", () => {
    it("keeps failed configuration visibly unavailable", async () => {
      renderPanel({
        getConfig: vi
          .fn<ExternalLibrariesApi["getConfig"]>()
          .mockRejectedValue(new ApiError(503, "Unavailable", "unavailable")),
      });

      expect(await screen.findByRole("alert")).toHaveTextContent(/could not|unavailable/i);
      expect(screen.queryByRole("switch")).toBeNull();
    });
    it("distinguishes failed source reads from empty sources", async () => {
      renderPanel({
        list: vi
          .fn<ExternalLibrariesApi["list"]>()
          .mockRejectedValue(new ApiError(503, "Unavailable", "unavailable")),
      });

      expect(await screen.findByRole("alert")).toHaveTextContent(/could not|unavailable/i);
      expect(screen.queryByText("No library sources yet")).toBeNull();
      expect(screen.getByRole("button", { name: /Retry/i })).toBeEnabled();
    });
    it("recovers source reads without an empty-state claim", async () => {
      renderPanel({
        list: vi
          .fn<ExternalLibrariesApi["list"]>()
          .mockRejectedValueOnce(new ApiError(503, "Unavailable", "unavailable"))
          .mockResolvedValue([aVolume()]),
      });
      const retry = await screen.findByRole("button", { name: "Retry" });
      expect(screen.queryByText("No library sources yet")).toBeNull();

      await userEvent.click(retry);

      expect(await screen.findByText("NAS models")).toBeVisible();
      expect(screen.queryByRole("alert")).toBeNull();
    });
    it("preserves healthy source rows read-only during transient failure", async () => {
      const api = stubApi({
        list: vi
          .fn<ExternalLibrariesApi["list"]>()
          .mockResolvedValueOnce([aVolume()])
          .mockRejectedValueOnce(new ApiError(503, "Unavailable", "unavailable"))
          .mockResolvedValue([aVolume()]),
      });
      const app = renderApp(<ExternalLibrariesPanel canEdit api={api} />);
      await screen.findByText("NAS models");

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: ["library-sources"] });
      });

      await screen.findByRole("alert");
      expect(screen.getByText("NAS models")).toBeVisible();
      expect(screen.getByRole("button", { name: "Scan now" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Add source" })).toBeDisabled();
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(screen.getByRole("button", { name: "Scan now" })).toBeEnabled());
    });
    it("hides denied private source state", async () => {
      const api = stubApi({
        list: vi
          .fn<ExternalLibrariesApi["list"]>()
          .mockResolvedValueOnce([aVolume()])
          .mockRejectedValueOnce(new ApiError(403, "Forbidden", "forbidden"))
          .mockResolvedValue([aVolume()]),
      });
      const app = renderApp(<ExternalLibrariesPanel canEdit api={api} />);
      await screen.findByText("NAS models");
      await userEvent.click(screen.getByRole("button", { name: "Remove library source" }));
      await screen.findByRole("dialog");

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: ["library-sources"] });
      });

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Library sources could not be loaded.",
      );
      expect(screen.queryByText("NAS models")).toBeNull();
      expect(screen.queryByRole("dialog")).toBeNull();
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByText("NAS models")).toBeVisible();
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    it("shares current compatible connection projections", async () => {
      const old = aStorageConnection({
        id: 4,
        name: "Old connection",
        kind: "s3",
        purpose: "library",
      });
      const current = aStorageConnection({
        id: 5,
        name: "Current connection",
        kind: "s3",
        purpose: "library",
      });
      const api = stubApi({
        listConnections: vi.fn<ExternalLibrariesApi["listConnections"]>().mockResolvedValue([old]),
      });
      const app = renderApp(<ExternalLibrariesPanel canEdit api={api} />);
      await screen.findByText("NAS models");
      await userEvent.selectOptions(screen.getByLabelText("Library source type"), "s3");
      expect(await screen.findByRole("option", { name: "Old connection" })).toBeInTheDocument();

      await act(async () => app.client.setQueryData(["storage-connections"], [current]));

      expect(await screen.findByRole("option", { name: "Current connection" })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "Old connection" })).toBeNull();
      expect(
        app.client.getQueryCache().findAll({ queryKey: ["storage-connections"] }),
      ).toHaveLength(1);
    });
    it("preserves a refused source removal", async () => {
      renderPanel({
        remove: vi
          .fn<ExternalLibrariesApi["remove"]>()
          .mockRejectedValue(new ApiError(409, "Remove refused", "remove_refused")),
      });
      await screen.findByText("NAS models");
      await userEvent.click(screen.getByRole("button", { name: "Remove library source" }));
      await userEvent.click(screen.getByRole("button", { name: "Remove" }));

      await waitFor(() =>
        expect(screen.getByRole("dialog")).toHaveTextContent(
          "Something went wrong reaching the server.",
        ),
      );
      expect(screen.getByText("NAS models")).toBeVisible();
      expect(screen.getByRole("dialog")).toBeVisible();
    });
    it("reports honest member capability", async () => {
      const api = stubApi();
      renderApp(<ExternalLibrariesPanel canEdit={false} api={api} />, { auth: memberSession() });

      expect(await screen.findByText(/Superuser access is required/)).toBeVisible();
      expect(screen.queryByText("NAS models")).toBeNull();
      expect(api.list).not.toHaveBeenCalled();
      expect(api.getConfig).not.toHaveBeenCalled();
    });
    it("hides private source rows immediately after permission loss", async () => {
      const api = stubApi();
      const app = renderApp(<ExternalLibrariesPanel canEdit api={api} />);
      await screen.findByText("NAS models");
      await userEvent.click(screen.getByRole("button", { name: "Remove library source" }));
      await screen.findByRole("dialog");

      app.rerender(<ExternalLibrariesPanel canEdit={false} api={api} />);

      expect(screen.queryByText("NAS models")).toBeNull();
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    it("preserves a newer create draft", async () => {
      const receipt = Promise.withResolvers<ExternalLibrary>();
      renderPanel({
        create: vi.fn<ExternalLibrariesApi["create"]>().mockReturnValue(receipt.promise),
      });
      await screen.findByText("NAS models");
      await userEvent.type(screen.getByLabelText("Source name"), "First source");
      await userEvent.type(screen.getByLabelText("Mounted folder path"), "/mnt/first");
      await userEvent.click(screen.getByRole("button", { name: "Add source" }));
      await userEvent.clear(screen.getByLabelText("Source name"));
      await userEvent.type(screen.getByLabelText("Source name"), "Next source");

      await act(async () => receipt.resolve(aVolume({ id: 8, name: "First source" })));

      expect(screen.getByLabelText("Source name")).toHaveValue("Next source");
      expect(await screen.findByText("First source")).toBeVisible();
    });
    it("rejects a retired create acknowledgement", async () => {
      const receipt = Promise.withResolvers<ExternalLibrary>();
      renderPanel({
        create: vi.fn<ExternalLibrariesApi["create"]>().mockReturnValue(receipt.promise),
      });
      await screen.findByText("NAS models");
      await userEvent.type(screen.getByLabelText("Source name"), "Retired source");
      await userEvent.type(screen.getByLabelText("Mounted folder path"), "/mnt/old");
      await userEvent.click(screen.getByRole("button", { name: "Add source" }));

      await act(async () => {
        clearLogin();
        receipt.resolve(aVolume({ id: 8, name: "Retired source" }));
      });

      expect(screen.queryByText("Library source added.")).toBeNull();
      expect(screen.queryByText("NAS models")).toBeNull();
    });
    it("aborts a disposed native source read", async () => {
      let delivered: AbortSignal | null | undefined;
      const app = renderApp(<ExternalLibrariesPanel canEdit />, {
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": (_url, options) => {
            delivered = options?.signal;
            return new Promise(() => {});
          },
        },
      });
      await waitFor(() => expect(delivered).toBeTruthy());

      app.unmount();

      expect(delivered).toMatchObject({ aborted: true });
    });
  });
});

describe("Source editing preconditions", () => {
  it.each([412, 503])("%s: reviews a source intent before revising", async (status) => {
    const original = aVolume();
    const latest = aVolume({
      name: "Other administrator",
      scan_schedule: "0 0 * * *",
      edit_version: 2,
    });
    let reads = 0;
    const headers: Headers[] = [];
    const app = renderApp(<ExternalLibrariesPanel canEdit />, {
      routes: {
        "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
        "GET /api/v1/storage-connections": json([]),
        "GET /api/v1/libraries": () => json([++reads === 1 ? original : latest]),
        "PATCH /api/v1/libraries/7": (_url, init) => {
          headers.push(new Headers(init?.headers));
          return headers.length === 1
            ? json({ detail: "edit_conflict" }, status)
            : json({ ...latest, enabled: false, edit_version: 3 });
        },
      },
    });
    await userEvent.click(await screen.findByRole("switch", { name: "Auto-scan enabled" }));
    await screen.findByRole("button", { name: "Review current values" });
    expect(screen.getByRole("switch", { name: "Auto-scan enabled" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Auto-scan enabled" })).toBeDisabled();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    const preview = await screen.findByRole("region", { name: "Latest saved version" });
    expect(within(preview).getByText("Other administrator")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Save revised changes" }));
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Auto-scan enabled" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Scan schedule Other administrator")).toHaveValue("0 0 * * *");
    expect(headers.map((value) => value.get("If-Match"))).toEqual([
      `"library-source-7-e${original.edit_epoch}-v1"`,
      `"library-source-7-e${original.edit_epoch}-v2"`,
    ]);
    expect(app.requestsWithMethod("PATCH").map((request) => JSON.parse(request.body))).toEqual([
      { enabled: false },
      { enabled: false },
    ]);
  });
  it("adopts current source values after a replaced preview", async () => {
    let reads = 0;
    const app = renderApp(<ExternalLibrariesPanel canEdit />, {
      routes: {
        "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
        "GET /api/v1/storage-connections": json([]),
        "GET /api/v1/libraries": () =>
          json([
            aVolume({
              name:
                ++reads === 1
                  ? "Original"
                  : reads === 2
                    ? "Replacement preview"
                    : "Current replacement",
              edit_epoch: reads === 1 ? "a".repeat(32) : "b".repeat(32),
            }),
          ]),
        "PATCH /api/v1/libraries/7": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(await screen.findByRole("switch", { name: "Auto-scan enabled" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review current values" }));
    await screen.findByText("Replacement preview");
    expect(screen.getByRole("button", { name: "Save revised changes" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Use current values" }));
    await screen.findByText("Current replacement");
    expect(screen.getByRole("switch", { name: "Auto-scan enabled" })).toBeChecked();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it.each([403, 404])("%s: retires unavailable source review", async (status) => {
    const app = renderApp(<ExternalLibrariesPanel canEdit />, {
      routes: {
        "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
        "GET /api/v1/storage-connections": json([]),
        "GET /api/v1/libraries": json([aVolume()]),
        "PATCH /api/v1/libraries/7": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(await screen.findByRole("switch", { name: "Auto-scan enabled" }));
    await screen.findByRole("button", { name: "Review current values" });
    app.route({ "GET /api/v1/libraries": json({ detail: "unavailable" }, status) });
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Review current values" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("NAS models")).not.toBeInTheDocument();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
});

describe("Source review lifetime", () => {
  it.each(["logout", "disposal"] as const)(
    "discards a held source review on %s",
    async (retirement) => {
      const response = Promise.withResolvers<Response>();
      const app = renderApp(<ExternalLibrariesPanel canEdit />, {
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/storage-connections": json([]),
          "GET /api/v1/libraries": json([aVolume()]),
          "PATCH /api/v1/libraries/7": json({ detail: "edit_conflict" }, 412),
        },
      });
      await userEvent.click(await screen.findByRole("switch", { name: "Auto-scan enabled" }));
      await screen.findByRole("button", { name: "Review current values" });
      app.route({ "GET /api/v1/libraries": () => response.promise });
      await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
      if (retirement === "logout") act(() => clearLogin());
      else app.unmount();
      await act(async () =>
        response.resolve(json([aVolume({ name: "Retired source", edit_version: 2 })])),
      );
      expect(screen.queryByText("Retired source")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save revised changes" }),
      ).not.toBeInTheDocument();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    },
  );
});
