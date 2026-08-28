/*
 * Mirroring a folder that PrintStash does not own.
 *
 * Everything here is qualified by one fact: the files live somewhere else — a
 * server directory, a NAS — and PrintStash only indexes them in place. So the
 * feature is off until an operator turns it on, and removing a volume must say
 * out loud that the files were not touched. A "Remove" that reads like a delete
 * is how somebody loses a NAS they spent a weekend organising.
 *
 * Real-time watching is a property of the filesystem, not a preference. A local
 * folder delivers file events; an NFS/SMB mount does not, and asking for events
 * there would leave the volume looking watched while it silently went stale. The
 * panel has to report which of the two a volume actually got.
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
      // vault flicker through a screen saying it has no shared volumes.
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

  describe("the volumes already mirrored", () => {
    it("names each mirrored folder", async () => {
      renderPanel();

      expect(await screen.findByText("NAS models")).toBeInTheDocument();
    });

    it("shows the path being indexed in place", async () => {
      renderPanel();

      expect(await screen.findByText("/mnt/nas/3d")).toBeInTheDocument();
    });

    it("says so when no folder is mirrored yet", async () => {
      renderPanel({ list: vi.fn<() => Promise<ExternalLibrary[]>>().mockResolvedValue([]) });

      expect(await screen.findByText("No shared volumes yet")).toBeInTheDocument();
    });

    it("marks a paused volume", async () => {
      renderPanel({
        list: vi
          .fn<() => Promise<ExternalLibrary[]>>()
          .mockResolvedValue([aVolume({ enabled: false })]),
      });

      expect(await screen.findByText("Paused")).toBeInTheDocument();
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

  describe("adding a folder", () => {
    it("refuses a folder with no name", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByPlaceholderText(/Absolute folder path/), "/mnt/x");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

      expect(api.create).not.toHaveBeenCalled();
    });

    it("refuses a name with no folder", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByPlaceholderText(/^Name/), "NAS");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

      expect(api.create).not.toHaveBeenCalled();
    });

    it("adds the folder the operator described", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByPlaceholderText(/^Name/), "Attic NAS");
      await user.type(screen.getByPlaceholderText(/Absolute folder path/), "/mnt/attic");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

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

    it("trims a path the operator pasted with whitespace", async () => {
      // A trailing space in a root path is a folder that does not exist, and the
      // failure surfaces much later as an empty scan.
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.type(screen.getByPlaceholderText(/^Name/), "  Attic  ");
      await user.type(screen.getByPlaceholderText(/Absolute folder path/), "  /mnt/attic  ");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

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
      await user.type(screen.getByPlaceholderText(/^Name/), "Attic");
      await user.type(screen.getByPlaceholderText(/Absolute folder path/), "/mnt/attic");
      await user.selectOptions(screen.getByDisplayValue(/Mirror subfolders/), "single");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

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
      await user.type(screen.getByPlaceholderText(/^Name/), "Attic");
      await user.type(screen.getByPlaceholderText(/Absolute folder path/), "/mnt/attic");
      await user.selectOptions(screen.getAllByRole("combobox")[2], "__custom__");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

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
      await user.type(screen.getByPlaceholderText(/^Name/), "Attic");
      await user.type(screen.getByPlaceholderText(/Absolute folder path/), "/etc");

      await user.click(screen.getByRole("button", { name: /Add library/ }));

      expect(await screen.findByText("Path not allowed.")).toBeInTheDocument();
    });
  });

  describe("removing a volume", () => {
    it("asks before removing", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: "Remove library" }));

      expect(api.remove).not.toHaveBeenCalled();
    });

    it("promises the files on the volume are left alone", async () => {
      // Without this the operator has to guess whether "Remove" deletes a NAS.
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText("NAS models");

      await user.click(screen.getByRole("button", { name: "Remove library" }));

      expect(
        await screen.findByText(/files on the shared volume are never touched/),
      ).toBeInTheDocument();
    });

    it("removes the volume once confirmed", async () => {
      const user = userEvent.setup();
      const { api } = renderPanel();
      await screen.findByText("NAS models");
      await user.click(screen.getByRole("button", { name: "Remove library" }));

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

      expect(await screen.findByRole("button", { name: "Remove library" })).toBeDisabled();
    });

    it("offers no per-volume schedule controls", async () => {
      // The per-volume row is not merely disabled — a viewer who cannot change
      // a schedule is never shown the control for it. Only the add-a-folder
      // form below stays on screen, disabled.
      renderPanel({}, false);

      await screen.findByText("NAS models");
      expect(screen.getAllByRole("combobox")).toHaveLength(3);
    });

    it("still shows what each volume is doing", async () => {
      // Read-only means read-only, not blind: the status is the whole reason a
      // non-admin opens this panel.
      renderPanel({}, false);

      expect(await screen.findByText("Watching (real-time)")).toBeInTheDocument();
    });
  });
});
