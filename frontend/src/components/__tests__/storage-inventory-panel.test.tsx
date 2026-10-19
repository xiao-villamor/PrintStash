/*
 * Storage insights must foreground useful capacity and file groups, distinguish
 * unknown capacity from a hard block, expose persisted history accessibly, and
 * require confirmation before removing expired staging.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
    locations: [],
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
    routes: { "GET /api/v1/storage/inventory": () => json(response) },
  });
}

async function storedHere(view: Pick<ReturnType<typeof renderApp>, "findByText">) {
  return (await view.findByText("Files stored here")).parentElement;
}

describe("Storage insights", () => {
  it("reserves the capacity layout while insights load", async () => {
    let finish: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    renderApp(<StorageInventoryPanel />, {
      routes: { "GET /api/v1/storage/inventory": () => pending },
    });

    expect(screen.getByRole("status", { name: "Loading storage insights…" })).toBeVisible();
    finish(json(report));
    expect(await screen.findByText("Files stored here")).toBeVisible();
    expect(screen.queryByRole("status", { name: "Loading storage insights…" })).toBeNull();
  });

  it("shows small capacity summaries in MB", async () => {
    setup({
      ...report,
      inventory: { ...report.inventory, unique_owned_bytes: 512 * 1024 },
    });

    expect((await screen.findByText("Files stored here")).parentElement).toHaveTextContent("<1 MB");
    expect(screen.getByText("Temporary files").parentElement).toHaveTextContent("0 MB");
  });

  it("explains unknown capacity", async () => {
    setup();
    expect(await screen.findByText("Capacity unknown")).toBeVisible();
    expect(screen.getByText(/2 objects have no recorded size/)).toBeVisible();
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
    expect(await screen.findByText("New allocations blocked")).toBeVisible();
  });
  it("separates what this computer holds from what a remote source reads in place", async () => {
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        locations: [
          {
            kind: "vault",
            label: "Vault",
            transport: "local",
            remote: false,
            library_id: null,
            connection: null,
            count: 0,
            logical_bytes: 439 * 1024 ** 2,
            unknown_size_count: 0,
          },
          {
            kind: "library",
            label: "NAS models",
            transport: "mounted",
            remote: false,
            library_id: 2,
            connection: null,
            count: 12,
            logical_bytes: 100 * 1024 ** 2,
            unknown_size_count: 0,
          },
          {
            kind: "library",
            label: "demo",
            transport: "s3",
            remote: true,
            library_id: 1,
            connection: "R2Test",
            count: 91,
            logical_bytes: 5 * 1024 ** 3,
            unknown_size_count: 0,
          },
        ],
      },
    });

    const places = await screen.findByRole("region", { name: "Where your files live" });
    expect(
      within(places).getByRole("img", {
        name: "539 MB on this computer · 5 GB in remote storage",
      }),
    ).toBeVisible();
    expect(within(places).getByText("On this computer")).toBeVisible();
    expect(within(places).getByText("In remote storage")).toBeVisible();
    expect(within(places).getByText("This computer")).toBeVisible();
    expect(within(places).getByText("Mounted folder")).toBeVisible();
    expect(within(places).getByText("S3 · R2Test")).toBeVisible();
    expect(within(places).getByText("91 files read in place")).toBeVisible();
  });

  it("offers no breakdown when the server reports no places", async () => {
    setup(report);

    await screen.findByText("What uses space");

    expect(screen.queryByRole("region", { name: "Where your files live" })).toBeNull();
  });

  it("shows measured free space prominently", async () => {
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        provider_capacity: {
          ...report.inventory.provider_capacity,
          available_bytes: 9 * 1024 ** 3,
        },
        volumes: [{ ...report.inventory.volumes[0], status: "available" }],
      },
    });

    expect(await screen.findByText("9 GB")).toBeVisible();
    expect(screen.getByText("Capacity available")).toBeVisible();
  });
  it("groups recorded stored sizes by purpose", async () => {
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        unique_owned_bytes: 95 * 1024 ** 2,
        backup_bytes: 5 * 1024 ** 2,
        buckets: [
          {
            category: "stl",
            lifecycle: "live",
            logical_bytes: 70 * 1024 ** 2,
            external_bytes: 20 * 1024 ** 2,
            count: 2,
          },
          {
            category: "thumbnail",
            lifecycle: "owned",
            logical_bytes: 30 * 1024 ** 2,
            external_bytes: 0,
            count: 1,
          },
          {
            category: "stl",
            lifecycle: "trash",
            logical_bytes: 10 * 1024 ** 2,
            external_bytes: 0,
            count: 1,
          },
        ],
      },
    });

    const breakdown = await screen.findByRole("region", { name: "What uses space" });
    expect(within(breakdown).getByText("Model and print files")).toBeVisible();
    expect(within(breakdown).getByText("50 MB")).toBeVisible();
    expect(within(breakdown).getByText("30 MB")).toBeVisible();
    expect(within(breakdown).getByText("10 MB")).toBeVisible();
    expect(within(breakdown).getByText("5 MB")).toBeVisible();
  });
  it("plots persisted history", async () => {
    setup({
      ...report,
      history: [
        {
          sampled_at: "2026-09-07T00:00:00Z",
          owned_bytes: 80,
          categories: { live_originals: 80, trash: 0, derived_cache: 0, backups: 0 },
        },
        {
          sampled_at: "2026-09-01T00:00:00Z",
          owned_bytes: 40,
          categories: { live_originals: 40, trash: 0, derived_cache: 0, backups: 0 },
        },
      ],
    });
    expect(
      await screen.findByRole("img", { name: "Recorded owned storage over time" }),
    ).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "Storage over time" })).getByText("+40 B"),
    ).toBeVisible();
  });
  it("explains when history cannot show a trend yet", async () => {
    setup();

    expect(
      await screen.findByText("Take another measurement to see changes over time."),
    ).toBeVisible();
    expect(
      screen.queryByRole("img", { name: "Recorded owned storage over time" }),
    ).not.toBeInTheDocument();
  });
  it("keeps storage measurement details accessible", async () => {
    const user = userEvent.setup();
    setup({
      ...report,
      inventory: {
        ...report.inventory,
        buckets: [
          { category: "step", lifecycle: "live", logical_bytes: 2048, external_bytes: 0, count: 1 },
        ],
      },
    });

    await user.click(await screen.findByText("Measurement details"));
    expect(screen.getByRole("heading", { name: "Files by type" })).toBeVisible();
    expect(screen.getByText("STEP files")).toBeVisible();
    expect(screen.getByText("In library")).toBeVisible();
    expect(screen.getByText(/Provider measurement/)).toBeVisible();
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
        "GET /api/v1/storage/inventory/collections?offset=0&limit=11": () =>
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

    expect(await view.findByRole("region", { name: "Recent storage activity" })).toBeVisible();
    expect(await view.findByText("backup · 1 KB")).toBeVisible();
    expect(await view.findByText(/Private collection name/)).toBeVisible();
    expect(view.getByText("2 KB")).toBeVisible();
    expect(view.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
  });
  it("shows recorded collection sizes with model counts", async () => {
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/collections?offset=0&limit=11": () =>
          json([
            {
              collection_id: 1,
              name: "Large",
              logical_bytes: 245 * 1024 ** 2,
              external_bytes: 0,
              model_count: 3,
            },
            {
              collection_id: 2,
              name: "Small",
              logical_bytes: 4 * 1024 ** 2,
              external_bytes: 0,
              model_count: 1,
            },
          ]),
      },
    });

    const collections = await view.findByRole("region", { name: "Collection storage" });
    expect(within(collections).getByRole("button", { name: /Large/ })).toHaveTextContent("245 MB");
    expect(within(collections).getByRole("button", { name: /Large/ })).toHaveTextContent(
      "3 models",
    );
    expect(within(collections).getByRole("button", { name: /Small/ })).toHaveTextContent("4 MB");
    expect(within(collections).getByRole("button", { name: /Small/ })).toHaveTextContent("1 model");
  });
  it("opens uncollected models", async () => {
    const user = userEvent.setup();
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/collections?offset=0&limit=11": () =>
          json([
            {
              collection_id: null,
              name: "Uncollected",
              logical_bytes: 251 * 1024 * 1024,
              external_bytes: 0,
              model_count: 3,
            },
            {
              collection_id: 7,
              name: "Small collection",
              logical_bytes: 42 * 1024,
              external_bytes: 0,
              model_count: 1,
            },
            {
              collection_id: 8,
              name: "No measured files",
              logical_bytes: 0,
              external_bytes: 0,
              model_count: 1,
            },
          ]),
        "GET /api/v1/storage/inventory/models?offset=0&limit=9&collection_id=0": () =>
          json([{ model_id: 9, name: "Bracket", logical_bytes: 1024 * 1024 }]),
      },
    });

    expect(await view.findByText("251 MB")).toBeVisible();
    expect(view.getByText("42 KB")).toBeVisible();
    expect(view.getByRole("button", { name: /No measured files/ })).toHaveTextContent("0 B");
    await user.click(view.getByRole("button", { name: /Uncollected/ }));
    const models = await view.findByRole("region", { name: "Models in Uncollected" });
    expect(within(models).getByText("Bracket")).toBeVisible();
    expect(within(models).getByText("1 MB")).toBeVisible();
    expect(within(models).getByRole("link", { name: /Bracket/ })).toHaveAttribute(
      "href",
      "/models/9",
    );
    expect(view.queryByRole("button", { name: /Small collection/ })).not.toBeInTheDocument();
    await user.click(within(models).getByRole("button", { name: "All collections" }));
    expect(view.getByRole("button", { name: /Small collection/ })).toBeVisible();
    expect(view.queryByRole("region", { name: "Models in Uncollected" })).not.toBeInTheDocument();
  });
  it("offers retry when a collection's models cannot load", async () => {
    const user = userEvent.setup();
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/collections?offset=0&limit=11": () =>
          json([
            {
              collection_id: 7,
              name: "Parts",
              logical_bytes: 1024,
              external_bytes: 0,
              model_count: 1,
            },
          ]),
        "GET /api/v1/storage/inventory/models?offset=0&limit=9&collection_id=7": () =>
          json({ detail: "unavailable" }, 503),
      },
    });

    await user.click(await view.findByRole("button", { name: /Parts/ }));
    expect(await view.findByRole("alert")).toHaveTextContent(
      "Could not load this collection's models.",
    );
    expect(view.getByRole("button", { name: "Retry" })).toBeVisible();
    await user.click(view.getByRole("button", { name: "All collections" }));
    expect(view.getByRole("button", { name: /Parts/ })).toBeVisible();
  });
  it("pages collections only when another collection exists", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 11 }, (_, index) => ({
      collection_id: index + 1,
      name: `Collection ${index + 1}`,
      logical_bytes: 1024 * (11 - index),
      external_bytes: 0,
      model_count: 1,
    }));
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/collections?offset=0&limit=11": () => json(rows),
        "GET /api/v1/storage/inventory/collections?offset=10&limit=11": () => json(rows.slice(10)),
      },
    });

    expect(await view.findByText("Collection 1")).toBeVisible();
    expect(view.getByText("Showing collections 1–10")).toBeVisible();
    await user.click(view.getByRole("button", { name: "Next page" }));
    expect(await view.findByText("Collection 11")).toBeVisible();
    expect(view.getByText("Showing collections 11–11")).toBeVisible();
    expect(view.getByRole("button", { name: "Next page" })).toBeDisabled();
    await user.click(view.getByRole("button", { name: "Previous page" }));
    expect(await view.findByText("Collection 1")).toBeVisible();
  });
  it("pages models only when another model exists", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 17 }, (_, index) => ({
      model_id: index + 1,
      name: `Bracket ${index + 1}`,
      logical_bytes: 1024 * (index + 1),
    }));
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/collections?offset=0&limit=11": () =>
          json([
            {
              collection_id: 7,
              name: "Parts",
              logical_bytes: 1024 * 66,
              external_bytes: 0,
              model_count: 17,
            },
          ]),
        "GET /api/v1/storage/inventory/models?offset=0&limit=9&collection_id=7": () =>
          json(rows.slice(0, 9)),
        "GET /api/v1/storage/inventory/models?offset=8&limit=9&collection_id=7": () =>
          json(rows.slice(8, 17)),
        "GET /api/v1/storage/inventory/models?offset=16&limit=9&collection_id=7": () =>
          json(rows.slice(16)),
      },
    });

    await user.click(await view.findByRole("button", { name: /Parts/ }));
    expect(await view.findByText("Bracket 1")).toBeVisible();
    expect(view.getByText("Showing models 1–8")).toBeVisible();
    await user.click(view.getByRole("button", { name: "Next page" }));
    expect(await view.findByText("Bracket 16")).toBeVisible();
    expect(view.getByText("Showing models 9–16")).toBeVisible();
    await user.click(view.getByRole("button", { name: "Next page" }));
    expect(await view.findByText("Bracket 17")).toBeVisible();
    expect(view.getByText("Showing models 17–17")).toBeVisible();
    expect(view.getByRole("button", { name: "Next page" })).toBeDisabled();
    await user.click(view.getByRole("button", { name: "Previous page" }));
    expect(await view.findByText("Bracket 16")).toBeVisible();
  });
  it("requires confirmation for staging cleanup", async () => {
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/cleanup-opportunities": () =>
          json([
            {
              owner: "staging",
              candidate_count: 1,
              candidate_bytes: 1024,
              action: "cleanup_expired_staging",
              available: true,
            },
          ]),
      },
    });
    const user = userEvent.setup();
    expect(await view.findByRole("region", { name: "Free up space" })).toHaveTextContent("1 KB");
    await user.click(view.getByRole("button", { name: "Clean up expired staging" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Uncertain files are retained");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("hides cleanup actions when no candidate exists", async () => {
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": () => json(report),
        "GET /api/v1/storage/inventory/cleanup-opportunities": () =>
          json([
            {
              owner: "staging",
              candidate_count: 0,
              candidate_bytes: 0,
              action: "cleanup_expired_staging",
              available: true,
            },
          ]),
      },
    });

    expect(await view.findByText("Files stored here")).toBeVisible();
    expect(view.queryByRole("region", { name: "Free up space" })).not.toBeInTheDocument();
    expect(
      view.queryByRole("button", { name: "Clean up expired staging" }),
    ).not.toBeInTheDocument();
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
  it("shows the kept measurement when the panel is opened again", async () => {
    const first = setup({
      ...report,
      inventory: { ...report.inventory, unique_owned_bytes: 5 * 1024 ** 2 },
    });
    expect(await storedHere(first)).toHaveTextContent("5 MB");
    first.rerender(<p>Elsewhere in settings</p>);
    expect(screen.queryByText("Files stored here")).toBeNull();

    first.rerender(<StorageInventoryPanel />);

    expect(screen.getByText("Files stored here").parentElement).toHaveTextContent("5 MB");
    expect(screen.queryByRole("status", { name: "Loading storage insights…" })).toBeNull();
    expect(
      first.requests().filter(({ url }) => url.endsWith("/api/v1/storage/inventory")),
    ).toHaveLength(1);
  });
  it("re-measures on refresh while the last figures stay on screen", async () => {
    const user = userEvent.setup();
    let finish: (response: Response) => void = () => {};
    let reads = 0;
    const view = renderApp(<StorageInventoryPanel />, {
      auth: adminSession(),
      routes: {
        "GET /api/v1/storage/inventory": (url) => {
          if (!url.endsWith("/inventory")) return json({ detail: "not_found" }, 404);
          reads += 1;
          if (reads === 1)
            return json({
              ...report,
              inventory: { ...report.inventory, unique_owned_bytes: 5 * 1024 ** 2 },
            });
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        },
        "POST /api/v1/storage/inventory/sample": json(report.inventory),
      },
    });
    expect(await storedHere(view)).toHaveTextContent("5 MB");

    await user.click(view.getByRole("button", { name: "Refresh measurement" }));

    await waitFor(() => expect(reads).toBe(2));
    expect(view.getByText("Files stored here").parentElement).toHaveTextContent("5 MB");
    expect(view.getByRole("button", { name: "Refresh measurement" })).toBeDisabled();
    finish(
      json({ ...report, inventory: { ...report.inventory, unique_owned_bytes: 7 * 1024 ** 2 } }),
    );
    await waitFor(() =>
      expect(view.getByText("Files stored here").parentElement).toHaveTextContent("7 MB"),
    );
    expect(view.requestsWithMethod("POST").map(({ url }) => url)).toEqual([
      "/api/v1/storage/inventory/sample",
    ]);
  });
  it("keeps the last figures when a refresh fails", async () => {
    const user = userEvent.setup();
    const view = setup({
      ...report,
      inventory: { ...report.inventory, unique_owned_bytes: 5 * 1024 ** 2 },
    });
    expect(await storedHere(view)).toHaveTextContent("5 MB");
    view.route({ "POST /api/v1/storage/inventory/sample": json({ detail: "boom" }, 500) });

    await user.click(view.getByRole("button", { name: "Refresh measurement" }));

    expect(await view.findByRole("alert")).toHaveTextContent(
      "Storage insights could not be loaded.",
    );
    expect(view.getByText("Files stored here").parentElement).toHaveTextContent("5 MB");
  });
});
