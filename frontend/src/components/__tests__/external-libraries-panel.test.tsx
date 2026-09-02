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
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ExternalLibrariesPanel,
  type ExternalLibrariesApi,
} from "@/components/external-libraries-panel";
import { renderApp } from "@/test-support/render";
import type {
  ExternalLibrary,
  ExternalLibraryCreate,
  ExternalLibraryScanSummary,
  ExternalLibraryUpdate,
  IngestJobStatus,
  IngestResponse,
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

function aJob(over: Partial<IngestJobStatus> = {}): IngestJobStatus {
  return {
    job_id: "job-1",
    state: "completed",
    model_id: null,
    file_id: null,
    error: null,
    started_at: FROZEN_NOW,
    finished_at: FROZEN_NOW,
    ...over,
  };
}

/**
 * The panel declares its API as a port precisely so a test can drive it without
 * a fetch layer in between. Each stub is overridable per test.
 */
function stubApi(over: Partial<ExternalLibrariesApi> = {}): ExternalLibrariesApi {
  return {
    isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockResolvedValue(true),
    setFeatureEnabled: vi.fn<(enabled: boolean) => Promise<void>>().mockResolvedValue(undefined),
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
      .fn<(id: number) => Promise<IngestResponse>>()
      .mockResolvedValue({ job_id: "job-1", state: "pending", message: "queued" }),
    jobStatus: vi.fn<(id: string) => Promise<IngestJobStatus>>().mockResolvedValue(aJob()),
    ...over,
  };
}

function renderPanel(over: Partial<ExternalLibrariesApi> = {}, canEdit = true) {
  const api = stubApi(over);
  const result = renderApp(<ExternalLibrariesPanel canEdit={canEdit} api={api} />);
  return { ...result, api };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ExternalLibrariesPanel", () => {
  describe("turning the feature on", () => {
    it("shows nothing until it knows whether the feature is on", () => {
      // Rendering the "off" state first and correcting it makes an enabled
      // vault flicker through a screen saying it has no library sources.
      renderPanel({
        isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockReturnValue(new Promise(() => {})),
      });

      expect(screen.queryByRole("switch")).toBeNull();
    });

    it("reads as off when the vault has it disabled", async () => {
      renderPanel({
        isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockResolvedValue(false),
      });

      const toggle = await screen.findByRole("switch");
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("presents sources as externally owned indexes", async () => {
      renderPanel({
        isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockResolvedValue(false),
      });

      expect(await screen.findByText("Library sources")).toBeInTheDocument();
      expect(screen.getByText(/without copying them into Vault storage/)).toBeInTheDocument();
      expect(screen.getByText(/never deleted by PrintStash/)).toBeInTheDocument();
    });

    it("lists nothing while the feature is off", async () => {
      // Volumes are only fetched once the feature is on, so a disabled vault
      // never asks the server about folders it will not mirror.
      const { api } = renderPanel({
        isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockResolvedValue(false),
      });

      await screen.findByRole("switch");
      expect(api.list).not.toHaveBeenCalled();
    });

    it("enables the feature when the operator turns it on", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel({
        isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockResolvedValue(false),
      });

      await user.click(await screen.findByRole("switch"));

      await waitFor(() => expect(api.setFeatureEnabled).toHaveBeenCalledWith(true));
    });

    it("puts the switch back when the server refuses", async () => {
      // A switch left "on" over a feature the server never enabled is a lie the
      // operator only discovers when nothing scans.
      const user = userEvent.setup();
      renderPanel({
        isFeatureEnabled: vi.fn<() => Promise<boolean>>().mockResolvedValue(false),
        setFeatureEnabled: vi
          .fn<(enabled: boolean) => Promise<void>>()
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
        expect(api.enroll).toHaveBeenCalledWith(7, { confirm_root_path: "/mnt/nas/3d" }),
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
      await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
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

      expect(await screen.findByText("Root marker conflict.")).toBeVisible();
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
        expect(api.update).toHaveBeenCalledWith(7, { scan_schedule: "0 0 * * *" }),
      );
    });

    it("pauses a volume without removing it", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("switch", { name: "Auto-scan enabled" }));

      await waitFor(() => expect(api.update).toHaveBeenCalledWith(7, { enabled: false }));
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

      await waitFor(() => expect(api.scan).toHaveBeenCalledWith(7));
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
          .fn<(id: string) => Promise<IngestJobStatus>>()
          .mockResolvedValue(aJob({ state: "failed", error: "root_path_missing" })),
      });
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: /Scan now/ }));

      expect(await screen.findByText("Root path missing.")).toBeInTheDocument();
    });

    it("re-reads the volume after a failed scan", async () => {
      // The failure itself is state the server recorded, so the row has to catch
      // up rather than keep showing the status from before the attempt.
      const user = userEvent.setup();
      const { api } = renderPanel({
        jobStatus: vi
          .fn<(id: string) => Promise<IngestJobStatus>>()
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
    });

    it("explains remote connections are reusable read-only sources", async () => {
      renderPanel({
        listConnections: vi.fn<() => Promise<StorageConnection[]>>().mockResolvedValue([]),
        createConnection: vi.fn<NonNullable<ExternalLibrariesApi["createConnection"]>>(),
        probeConnection: vi.fn<NonNullable<ExternalLibrariesApi["probeConnection"]>>(),
        deleteConnection: vi.fn<NonNullable<ExternalLibrariesApi["deleteConnection"]>>(),
      });

      expect(await screen.findByText("Remote source connections")).toBeInTheDocument();
      expect(screen.getByText(/reusable connections for read-only S3/)).toBeInTheDocument();
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
        expect(api.create).toHaveBeenCalledWith({
          name: "Attic NAS",
          root_path: "/mnt/attic",
          scan_schedule: "0 * * * *",
          watch_mode: "auto",
          collection_mode: "mirror",
        }),
      );
    });

    it("adds a read-only remote S3 source through a reusable profile", async () => {
      const user = userEvent.setup();
      const profile: StorageConnection = {
        id: 41,
        name: "TrueNAS MinIO",
        kind: "s3",
        configuration: { bucket: "models" },
        secret_fields_set: ["access_key", "secret_key"],
        enabled: true,
      };
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
        expect(api.create).toHaveBeenCalledWith({
          name: "Remote catalogue",
          root_path: undefined,
          scan_schedule: "0 * * * *",
          watch_mode: "off",
          collection_mode: "mirror",
          source_kind: "s3",
          connection_id: 41,
          source_prefix: "production",
        }),
      );
    });

    it("keeps secrets write-only across remote profile verification", async () => {
      const user = userEvent.setup();
      const profile: StorageConnection = {
        id: 42,
        name: "AWS archive",
        kind: "s3",
        configuration: { bucket: "archive" },
        secret_fields_set: ["access_key", "secret_key"],
        enabled: true,
      };
      const createConnection = vi
        .fn<NonNullable<ExternalLibrariesApi["createConnection"]>>()
        .mockResolvedValue(profile);
      const probeConnection = vi
        .fn<NonNullable<ExternalLibrariesApi["probeConnection"]>>()
        .mockResolvedValue({ ok: true });
      renderPanel({
        listConnections: vi.fn<() => Promise<StorageConnection[]>>().mockResolvedValue([]),
        createConnection,
        probeConnection,
        deleteConnection: vi
          .fn<NonNullable<ExternalLibrariesApi["deleteConnection"]>>()
          .mockResolvedValue(undefined),
      });
      await screen.findByText("Remote source connections");
      await user.type(screen.getByLabelText("Connection name"), "AWS archive");
      await user.type(screen.getByLabelText("S3 bucket"), "archive");
      await user.type(screen.getByLabelText("S3 access key"), "ACCESS");
      await user.type(screen.getByLabelText("S3 secret key"), "SECRET");

      await user.click(screen.getByRole("button", { name: "Save and verify connection" }));

      await waitFor(() =>
        expect(createConnection).toHaveBeenCalledWith(
          expect.objectContaining({
            name: "AWS archive",
            kind: "s3",
            secrets: { access_key: "ACCESS", secret_key: "SECRET" },
          }),
        ),
      );
      expect(probeConnection).toHaveBeenCalledWith(42);
      expect(await screen.findByText(/credentials stored: access_key, secret_key/)).toBeVisible();
      expect(screen.queryByDisplayValue("SECRET")).toBeNull();
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

      await waitFor(() => expect(api.remove).toHaveBeenCalledWith(7));
    });
  });

  describe("a viewer who cannot administer the vault", () => {
    it("offers no way to turn the feature on", async () => {
      renderPanel({}, false);

      const [featureToggle] = await screen.findAllByRole("switch");
      expect(featureToggle).toBeDisabled();
    });

    it("offers no way to scan a volume", async () => {
      renderPanel({}, false);

      expect(await screen.findByRole("button", { name: /Scan now/ })).toBeDisabled();
    });

    it("offers no way to remove one", async () => {
      renderPanel({}, false);

      expect(await screen.findByRole("button", { name: "Remove library source" })).toBeDisabled();
    });

    it("offers no per-volume schedule controls", async () => {
      // The per-volume row is not merely disabled — a viewer who cannot change
      // a schedule is never shown the control for it. Only the add-a-folder
      // form below stays on screen, disabled.
      renderPanel({}, false);

      await screen.findByText("NAS models");
      expect(screen.getAllByRole("combobox")).toHaveLength(4);
    });

    it("still shows what each volume is doing", async () => {
      // Read-only means read-only, not blind: the status is the whole reason a
      // non-admin opens this panel.
      renderPanel({}, false);

      expect(await screen.findByText("Watching (real-time)")).toBeInTheDocument();
    });
  });
});
