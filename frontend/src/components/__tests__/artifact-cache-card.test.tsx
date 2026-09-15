/** Cache controls preserve active readers and separate policy changes from clearing. */
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactCacheCard } from "@/components/artifact-cache-card";
import type { ArtifactCacheRead } from "@/lib/api/artifact-cache";
import { json, renderApp } from "@/test-support/render";
import { anArtifactCache } from "@/test-support/factories";

const INITIAL = anArtifactCache({ policy: { ...anArtifactCache().policy, enabled: true } });

describe("ArtifactCacheCard", () => {
  it("hides cache tuning while disabled", async () => {
    renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(anArtifactCache({ usage: { bytes: 0 } })),
      },
    });
    expect(await screen.findByText(/Caching is off/)).toBeVisible();
    expect(
      screen.queryByRole("spinbutton", { name: "Cache size limit (GB)" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear cached files" })).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Keep downloaded files on this machine" }),
    );
    expect(screen.getByRole("spinbutton", { name: "Cache size limit (GB)" })).toBeVisible();
  });
  it("displays cache space when enabled", async () => {
    renderApp(<ArtifactCacheCard />, {
      routes: { "GET /api/v1/config/artifact-cache": json(INITIAL) },
    });
    expect(await screen.findByText("Downloaded copies")).toBeVisible();
    expect(screen.getByText("100 B")).toBeVisible();
    expect(screen.getByText(/active reads/)).not.toBeVisible();
  });
  it.each([
    { label: "empty", bytes: 0, count: 0 },
    { label: "occupied", bytes: 100, count: 1 },
  ])("offers cleanup for $label cache", async ({ bytes, count }) => {
    renderApp(<ArtifactCacheCard />, {
      routes: { "GET /api/v1/config/artifact-cache": json({ ...INITIAL, usage: { bytes } }) },
    });
    await screen.findByText("Downloaded copies");
    expect(screen.queryAllByRole("button", { name: "Clear cached files" })).toHaveLength(count);
  });
  it("distinguishes unavailable cache", async () => {
    renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json({
          ...INITIAL,
          available: false,
          health: "unavailable",
        }),
      },
    });
    expect(await screen.findByText(/Caching is enabled but not available/)).toBeVisible();
  });

  it("displays cache limits in GB", async () => {
    renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": () =>
          new Response(
            JSON.stringify(
              anArtifactCache({
                policy: { ...INITIAL.policy, max_bytes: 10 * 1024 ** 3, headroom_bytes: 1024 ** 3 },
              }),
            ),
            { headers: { "Content-Type": "application/json" } },
          ),
      },
    });
    expect(await screen.findByRole("spinbutton", { name: "Cache size limit (GB)" })).toHaveValue(
      10,
    );
    expect(screen.getByRole("spinbutton", { name: "Keep free on disk (GB)" })).toHaveValue(1);
  });

  it.each([
    { label: "fractional", size: "1.25", expected: 1342177280 },
    { label: "zero", size: "0", expected: 0 },
  ])("saves $label GB limits as bytes", async ({ size, expected }) => {
    let persisted = INITIAL;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async (policy) => {
            persisted = { ...INITIAL, policy };
            return persisted;
          },
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    const limit = await screen.findByRole("spinbutton", { name: "Cache size limit (GB)" });
    const free = screen.getByRole("spinbutton", { name: "Keep free on disk (GB)" });
    await userEvent.clear(limit);
    await userEvent.type(limit, size);
    await userEvent.clear(free);
    await userEvent.type(free, size);
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    expect(persisted.policy).toEqual({
      ...INITIAL.policy,
      max_bytes: expected,
      headroom_bytes: expected,
    });
  });

  it.each([
    { label: "empty", value: "" },
    { label: "negative", value: "-1" },
    { label: "oversized", value: "999999999999" },
  ])("rejects a $label cache limit", async ({ value }) => {
    let saves = 0;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async () => {
            saves += 1;
            return INITIAL;
          },
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    const limit = await screen.findByRole("spinbutton", { name: "Cache size limit (GB)" });
    await userEvent.clear(limit);
    fireEvent.change(limit, { target: { value } });
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    expect(limit).toBeInvalid();
    expect(saves).toBe(0);
  });

  it("refreshes the GB fields when defaults are restored", async () => {
    const defaults = anArtifactCache({
      policy: { ...INITIAL.policy, max_bytes: 10 * 1024 ** 3, headroom_bytes: 1024 ** 3 },
    });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async () => INITIAL,
          reset: async () => defaults,
          clear: async () => INITIAL,
        }}
      />,
    );
    await userEvent.click(await screen.findByText("Advanced cache settings"));
    await userEvent.click(screen.getByRole("button", { name: "Restore cache defaults" }));
    expect(screen.getByRole("spinbutton", { name: "Cache size limit (GB)" })).toHaveValue(10);
    expect(screen.getByRole("spinbutton", { name: "Keep free on disk (GB)" })).toHaveValue(1);
  });

  it("reveals optional cache controls on demand", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    await screen.findByRole("button", { name: "Save cache settings" });
    expect(screen.getByText("Maximum cached files")).not.toBeVisible();
    expect(screen.getByText("Publication failures")).not.toBeVisible();
    await userEvent.click(screen.getByText("Advanced cache settings"));
    expect(screen.getByRole("spinbutton", { name: "Maximum cached files" })).toBeVisible();
    await userEvent.click(screen.getByText("Cache diagnostics"));
    expect(screen.getByText("Publication failures")).toBeVisible();
  });

  it("disables caching without clearing files", async () => {
    let persisted = INITIAL;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async (policy) => {
            persisted = { ...INITIAL, policy };
            return persisted;
          },
          reset: async () => INITIAL,
          clear: async () => {
            throw new Error("unexpected clear");
          },
        }}
      />,
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Keep downloaded files on this machine" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    expect(persisted.policy.enabled).toBe(false);
    expect(await screen.findByText("100 B")).toBeInTheDocument();
  });

  it("shows remaining leased bytes after clear", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => ({ ...INITIAL, usage: { bytes: 25, entries: 1, leases: 1 } }),
        }}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Clear cached files" }));
    expect(await screen.findByText("25 B")).toBeVisible();
    await userEvent.click(screen.getByText("Cache diagnostics"));
    expect(screen.getByText(/1 active reads/)).toBeVisible();
  });

  it("shows restart requirement after changing root", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => ({ ...INITIAL, restart_required: true }),
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    expect(
      await screen.findByText("Restart PrintStash to use the new cache folder."),
    ).toBeInTheDocument();
  });

  it("allows retry after loading fails", async () => {
    let failed = true;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => {
            if (failed) throw new Error("unavailable");
            return INITIAL;
          },
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    const retry = await screen.findByRole("button", { name: "Retry" });
    failed = false;
    await userEvent.click(retry);
    expect(
      await screen.findByRole("checkbox", { name: "Keep downloaded files on this machine" }),
    ).toBeInTheDocument();
  });

  it("reports a malformed cache response without breaking Settings", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          // SAFETY: This test deliberately violates the wire contract to verify containment.
          read: async () => ({}) as ArtifactCacheRead,
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cache settings could not be loaded.",
    );
  });
});

describe("ArtifactCacheCard observability", () => {
  it("shows cache effectiveness with policy source", async () => {
    const observed = anArtifactCache({
      source: "database",
      usage: {
        bytes: 100,
        entries: 1,
        hit_ratio_percent: 75,
        bytes_saved: 300,
        completed_fills: 1,
        publication_failures: 5,
        corruptions: 2,
        bypasses: 3,
        evictions: 4,
        last_verification: Date.parse("2026-01-01T00:00:00Z") / 1000,
      },
    });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async () => observed,
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    await userEvent.click(await screen.findByText("Advanced cache settings"));
    await userEvent.click(screen.getByText("Cache diagnostics"));
    expect(await screen.findByText(/Policy source: Saved settings/)).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText(/Download traffic saved: 300 B/)).toBeInTheDocument();
    expect(screen.getByText("Publication failures")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText(/Last verification:/)).not.toHaveTextContent("No cached files");
  });

  it("shows safe reclamation progress", async () => {
    const observed = anArtifactCache({ usage: { pending_eviction_bytes: 100, leases: 1 } });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async () => observed,
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    expect(await screen.findByText(/100 B will be freed/)).toBeInTheDocument();
  });

  it("clears a transient polling failure after polling recovers", async () => {
    vi.useFakeTimers();
    let reads = 0;
    const observed = anArtifactCache({ usage: { pending_eviction_bytes: 100 } });
    try {
      renderApp(
        <ArtifactCacheCard
          api={{
            read: async () => {
              reads += 1;
              if (reads === 2) throw new Error("temporary poll failure");
              return observed;
            },
            save: async () => observed,
            reset: async () => observed,
            clear: async () => observed,
          }}
        />,
      );
      await act(async () => Promise.resolve());
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("distinguishes cache degradation from original storage", async () => {
    const observed = anArtifactCache({ health: "corrupt_index" });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async () => observed,
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    expect(await screen.findByText(/Cache needs attention/)).toHaveTextContent(
      "Original Vault storage remains authoritative",
    );
  });
});
