/*
 * Storage insights must distinguish unknown capacity from a hard block, expose
 * persisted history accessibly, and require confirmation before removing any
 * expired staging. The tests exercise those operator-visible safety contracts.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { StorageInventoryPanel } from "@/components/storage-inventory-panel";
import { adminSession, json, renderApp } from "@/test-support/render";
import type { StorageInventoryReport } from "@/lib/api/storage-inventory";

const report: StorageInventoryReport = {
  inventory: {
    schema_version: 1,
    generated_at: "2026-09-07T00:00:00Z",
    measured_at: null,
    target_ref: "test",
    logical_bytes: 0,
    external_referenced_bytes: 0,
    unique_owned_bytes: 0,
    unknown_object_count: 2,
    temporary_bytes: 0,
    backup_bytes: 0,
    measured_provider_bytes: null,
    method: "database ownership census",
    confidence: "known sizes only",
    provider_capacity: {
      status: "unknown",
      total_bytes: null,
      used_bytes: null,
      available_bytes: null,
      quota_bytes: null,
      measured_at: null,
      method: "unsupported",
      reliability: "unknown",
      error: null,
    },
    latest_audit: null,
    buckets: [],
    volumes: [
      {
        domain_id: "test",
        roles: ["vault"],
        total_bytes: null,
        free_bytes: null,
        reserved_bytes: 0,
        headroom_bytes: 0,
        status: "unknown",
        method: "unavailable",
      },
    ],
  },
  history: [],
  forecast: {
    status: "insufficient_data",
    days_remaining: null,
    bytes_per_day: null,
    sample_count: 0,
    window_days: 0,
    confidence: "insufficient",
    threshold_at: null,
  },
};

function setup(response = report) {
  return renderApp(<StorageInventoryPanel />, {
    auth: adminSession(),
    routes: {
      "GET /api/v1/storage/inventory": () => json(response),
      "GET /api/v1/storage/inventory/cleanup-opportunities": json([]),
    },
  });
}

describe("Storage insights", () => {
  it("distinguishes a failed cleanup check from an empty result", async () => {
    renderApp(<StorageInventoryPanel />, {
      routes: {
        "GET /api/v1/storage/inventory": json(report),
        "GET /api/v1/storage/inventory/cleanup-opportunities": json({ detail: "unavailable" }, 503),
      },
    });
    expect(
      await screen.findByText(
        "Cleanup could not be checked. Refresh the measurement to try again.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("No files need cleaning up right now.")).not.toBeInTheDocument();
  });
  it("omits empty cleanup actions", async () => {
    setup();
    expect(await screen.findByText("No files need cleaning up right now.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Clean up temporary files" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear derived cache" })).not.toBeInTheDocument();
  });
  it("prioritizes library space", async () => {
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        unique_owned_bytes: 2 * 1024 ** 3,
        volumes: [
          { ...report.inventory.volumes[0], free_bytes: 10 * 1024 ** 3, status: "available" },
        ],
      },
    });
    expect(await screen.findByText("2 GB")).toBeVisible();
    expect(screen.getByText("10 GB")).toBeVisible();
    expect(screen.getByText("Linked files")).not.toBeVisible();
  });
  it("keeps the storage summary readable before opening diagnostics", async () => {
    setup();
    expect(await screen.findByText("Library files")).toBeVisible();
    expect(screen.getByText("Growth forecast")).not.toBeVisible();
    expect(screen.getByText("No files need cleaning up right now.")).toBeVisible();
    await userEvent.click(screen.getByText("Storage breakdown and diagnostics"));
    expect(screen.getByText("Growth forecast")).toBeVisible();
  });
  it("explains unknown capacity", async () => {
    setup();
    expect(await screen.findByText("Free space is unknown")).toBeVisible();
    expect(screen.getByText("Some file sizes are unknown, so usage may be higher.")).toBeVisible();
  });
  it("shows only aggregate unattributed audit evidence", async () => {
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        latest_audit: {
          run_id: 9,
          completed_at: "2026-09-07T00:00:00Z",
          unclaimed_object_count: 3,
          unclaimed_bytes: 4096,
          unknown_size_count: 1,
          method: "vault_audit_findings",
        },
      },
    });

    expect(await screen.findByText(/Latest audit found 3/)).toHaveTextContent(
      "Known size: 4 KB. Unknown sizes: 1.",
    );
  });
  it("explains allocation blockers", async () => {
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        volumes: [{ ...report.inventory.volumes[0], status: "blocked", free_bytes: 0 }],
      },
    });
    expect(await screen.findByText("Storage is full — free up space to continue")).toBeVisible();
  });
  it("plots persisted history", async () => {
    setup({
      ...report,
      history: [
        {
          sampled_at: "2026-09-01T00:00:00Z",
          owned_bytes: 40,
          categories: { live_originals: 40, trash: 0, derived_cache: 0, backups: 0 },
        },
        {
          sampled_at: "2026-09-07T00:00:00Z",
          owned_bytes: 80,
          categories: { live_originals: 80, trash: 0, derived_cache: 0, backups: 0 },
        },
      ],
    });
    await userEvent.click(await screen.findByText("Storage breakdown and diagnostics"));
    expect(
      await screen.findByRole("img", { name: "Recorded owned storage over time" }),
    ).toBeVisible();
  });

  it("shows a privacy-safe reservation beside the paginated Collection drilldown", async () => {
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/activity": () =>
          json({
            active_reservations: [
              {
                operation_kind: "backup",
                required_bytes: 1024,
                roles: ["backup archive"],
                durable: false,
                created_at: "2026-09-07T00:00:00Z",
                expires_at: "2026-09-07T01:00:00Z",
              },
            ],
            recent_denials: [],
          }),
        "GET /api/v1/storage/inventory/collections?offset=0&limit=10": () =>
          json([
            {
              collection_id: 7,
              name: "Private collection name",
              logical_bytes: 2048,
              external_bytes: 0,
              model_count: 1,
            },
          ]),
      },
    });

    await userEvent.click(await screen.findByText("Storage breakdown and diagnostics"));
    expect(await view.findByText("backup · 0 GB")).toBeVisible();
    expect(await view.findByText(/Private collection name/)).toBeVisible();
  });
  it("requires confirmation for staging cleanup", async () => {
    const view = setup();
    view.route({
      "GET /api/v1/storage/inventory/cleanup-opportunities": json([
        {
          owner: "staging",
          candidate_count: 1,
          candidate_bytes: 1024,
          action: "cleanup_staging",
          available: true,
        },
      ]),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Clean up temporary files" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Uncertain files are retained");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("clears only verified rebuildable cache after confirmation", async () => {
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/cleanup-opportunities": () =>
          json([
            {
              owner: "cache",
              candidate_count: 2,
              candidate_bytes: 4096,
              action: "cleanup_derived_cache",
              available: true,
            },
          ]),
        "POST /api/v1/storage/inventory/cleanup-cache": json({
          candidates: 2,
          enqueued: 2,
        }),
      },
    });
    const user = userEvent.setup();
    await user.click(await view.findByRole("button", { name: "Clear derived cache" }));
    expect(await view.findByRole("dialog")).toHaveTextContent(
      "Thumbnails and original Artifacts are retained",
    );
    await user.click(view.getByRole("button", { name: "Clean up" }));
    expect(await view.findByRole("status")).toHaveTextContent(
      "2 derived cache objects queued for verified cleanup.",
    );
  });
});
