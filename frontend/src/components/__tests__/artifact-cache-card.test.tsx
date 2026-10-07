/** Cache controls preserve active readers and separate policy changes from clearing. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactCacheCard } from "@/components/artifact-cache-card";
import type { ArtifactCacheRead } from "@/lib/api/artifact-cache";
import { clearLogin } from "@/lib/auth-store";
import { json, renderApp } from "@/test-support/render";
import { anArtifactCache } from "@/test-support/factories";

const INITIAL = anArtifactCache();

describe("ArtifactCacheCard", () => {
  it("reserves the cache controls while policy loads", async () => {
    let finish: (value: ArtifactCacheRead) => void = () => {};
    const pending = new Promise<ArtifactCacheRead>((resolve) => {
      finish = resolve;
    });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: () => pending,
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );

    expect(screen.getByRole("status", { name: "Loading cache settings…" })).toBeVisible();
    finish(INITIAL);
    expect(
      await screen.findByRole("checkbox", { name: "Enable remote Artifact cache" }),
    ).toBeVisible();
    expect(screen.queryByRole("status", { name: "Loading cache settings…" })).toBeNull();
  });

  it("shows large cache limits in GB", async () => {
    const observed = anArtifactCache({
      policy: {
        ...INITIAL.policy,
        max_bytes: 10 * 1024 ** 3,
        headroom_bytes: 1024 ** 3,
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

    await userEvent.click(await screen.findByRole("tab", { name: "Cache limits" }));

    expect(screen.getByRole("spinbutton", { name: "Maximum cache size (GB)" })).toHaveValue(10);
    expect(screen.getByRole("spinbutton", { name: "Minimum free space (GB)" })).toHaveValue(1);
  });

  it("converts an edited MB limit to bytes", async () => {
    const observed = anArtifactCache({
      policy: { ...INITIAL.policy, max_bytes: 256 * 1024 ** 2 },
    });
    let savedBytes = -1;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async (policy) => {
            savedBytes = policy.max_bytes;
            return { ...observed, policy };
          },
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Cache limits" }));
    const limit = screen.getByRole("spinbutton", { name: "Maximum cache size (MB)" });

    await user.clear(limit);
    await user.type(limit, "0.5");
    await user.click(screen.getByRole("button", { name: "Save cache settings" }));

    expect(savedBytes).toBe(512 * 1024);
  });

  it("preserves byte values when a size field is untouched", async () => {
    let savedMaxBytes = -1;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async (policy) => {
            savedMaxBytes = policy.max_bytes;
            return { ...INITIAL, policy };
          },
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Save cache settings" }));

    expect(savedMaxBytes).toBe(1000);
  });

  it("saves enabled policy without clearing files", async () => {
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
      await screen.findByRole("checkbox", { name: "Enable remote Artifact cache" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    expect(persisted.policy.enabled).toBe(true);
    expect(await screen.findByText(/<1 MB cached/)).toBeVisible();
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
    expect(await screen.findByText(/<1 MB cached/)).toHaveTextContent("1 active reads");
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
      await screen.findByRole("checkbox", { name: "Enable remote Artifact cache" }),
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
        bytes_saved: 2 * 1024 ** 3,
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
    expect(await screen.findByRole("tabpanel", { name: "Overview" })).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Cache limits" }));
    expect(screen.getByText(/Policy source: Saved settings/)).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("tabpanel", { name: "Activity" })).toBeVisible();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Provider data saved").parentElement).toHaveTextContent("2 GB");
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
    expect(await screen.findByText(/<1 MB wait for active reads/)).toBeInTheDocument();
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
      await act(async () => vi.advanceTimersByTimeAsync(1010));
      expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
      await act(async () => vi.advanceTimersByTimeAsync(1010));
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

describe("Artifact cache ownership", () => {
  it("shares cancellable cache maintenance reads", async () => {
    vi.useFakeTimers();
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    try {
      const view = renderApp(
        <>
          <ArtifactCacheCard />
          <ArtifactCacheCard />
        </>,
        {
          routes: {
            "GET /api/v1/config/artifact-cache": (_url, init) => {
              signal = init?.signal;
              return pending.promise;
            },
          },
        },
      );
      await act(async () => vi.advanceTimersByTimeAsync(3000));
      expect(view.requestsWithMethod("GET")).toHaveLength(1);
      view.unmount();
      expect(signal?.aborted).toBe(true);
      await act(async () => pending.resolve(json(INITIAL)));
    } finally {
      vi.useRealTimers();
    }
  });
  it("clears obsolete cache reads before acknowledging writes", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const saved = anArtifactCache({
      edit_version: 2,
      policy: { ...INITIAL.policy, max_bytes: 20 * 1024 ** 2 },
    });
    const view = renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(INITIAL),
        "PUT /api/v1/config/artifact-cache": json(saved),
      },
    });
    await userEvent.click(await screen.findByRole("tab", { name: "Cache limits" }));
    const input = screen.getByRole("spinbutton", { name: "Maximum cache size (MB)" });
    await userEvent.clear(input);
    await userEvent.type(input, "20");
    view.route({
      "GET /api/v1/config/artifact-cache": (_url, init) => {
        signal = init?.signal;
        return pending.promise;
      },
    });
    await act(async () => {
      void view.client.invalidateQueries();
    });
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    expect(await screen.findByText("Artifact cache settings updated.")).toBeVisible();
    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(json(INITIAL)));
    expect(input).toHaveValue(20);
    expect(view.client.getQueryData(["artifact-cache"])).toEqual(saved);
  });
  it("retires cache reads on unmount", async () => {
    const response = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const view = renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": (_url, init) => {
          signal = init?.signal;
          return response.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => response.resolve(json(INITIAL)));
  });
  it("preserves cache policy draft during usage refresh", async () => {
    const view = renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(INITIAL),
      },
    });
    await userEvent.click(await screen.findByRole("tab", { name: "Cache limits" }));
    const input = screen.getByRole("spinbutton", { name: "Maximum cache size (MB)" });
    await userEvent.clear(input);
    await userEvent.type(input, "20");
    view.route({
      "GET /api/v1/config/artifact-cache": json(
        anArtifactCache({ usage: { bytes: 200, leases: 2 } }),
      ),
    });
    await act(async () => {
      await view.client.invalidateQueries();
    });
    expect(input).toHaveValue(20);
    expect(await screen.findByText(/2 active reads/)).toBeVisible();
  });
  it("preserves cache policy draft when clearing bytes", async () => {
    renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(INITIAL),
        "POST /api/v1/config/artifact-cache/clear": json(anArtifactCache({ usage: { bytes: 0 } })),
      },
    });
    await userEvent.click(await screen.findByRole("tab", { name: "Cache limits" }));
    const input = screen.getByRole("spinbutton", { name: "Maximum cache size (MB)" });
    await userEvent.clear(input);
    await userEvent.type(input, "20");
    await userEvent.click(screen.getByRole("button", { name: "Clear cached files" }));
    expect(await screen.findByText(/0 MB cached/)).toBeVisible();
    expect(input).toHaveValue(20);
  });
  it.each([412, 503])(
    "requires explicit review after an unaccepted cache save (%i)",
    async (status) => {
      const view = renderApp(<ArtifactCacheCard />, {
        routes: {
          "GET /api/v1/config/artifact-cache": json(INITIAL),
          "PUT /api/v1/config/artifact-cache": json({ detail: "edit_conflict" }, status),
        },
      });
      await userEvent.click(await screen.findByRole("tab", { name: "Cache limits" }));
      const input = screen.getByRole("spinbutton", { name: "Maximum cache size (MB)" });
      await userEvent.clear(input);
      await userEvent.type(input, "20");
      await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
      expect(await screen.findByRole("button", { name: "Review current values" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Save cache settings" })).toBeDisabled();
      expect(input).toHaveValue(20);
      view.route({
        "GET /api/v1/config/artifact-cache": json(
          anArtifactCache({ policy: { ...INITIAL.policy, max_bytes: 10 * 1024 ** 2 } }),
        ),
      });
      await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
      await userEvent.click(await screen.findByRole("button", { name: "Use current values" }));
      expect(input).toHaveValue(10);
      expect(view.requestsWithMethod("PUT")).toHaveLength(1);
    },
  );
  it("saves only revised cache fields over reviewed policy", async () => {
    const view = renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(INITIAL),
        "PUT /api/v1/config/artifact-cache": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(await screen.findByRole("tab", { name: "Cache limits" }));
    const size = screen.getByRole("spinbutton", { name: "Maximum cache size (MB)" });
    await userEvent.clear(size);
    await userEvent.type(size, "20");
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    const reviewed = anArtifactCache({
      edit_version: 2,
      policy: { ...INITIAL.policy, max_entries: 100 },
    });
    view.route({
      "GET /api/v1/config/artifact-cache": json(reviewed),
      "PUT /api/v1/config/artifact-cache": json(
        anArtifactCache({
          edit_version: 3,
          policy: { ...reviewed.policy, max_bytes: 20 * 1024 ** 2 },
        }),
      ),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review current values" }));
    await userEvent.click(await screen.findByRole("button", { name: "Save revised changes" }));
    expect(await screen.findByText("Artifact cache settings updated.")).toBeVisible();
    expect(JSON.parse(view.requestsWithMethod("PUT")[1]!.body)).toEqual({
      ...reviewed.policy,
      max_bytes: 20 * 1024 ** 2,
    });
    expect(screen.getByRole("spinbutton", { name: "Maximum cached files" })).toHaveValue(100);
  });
  it("forbids cache draft replay across restored epochs", async () => {
    const view = renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(INITIAL),
        "PUT /api/v1/config/artifact-cache": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Enable remote Artifact cache" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    view.route({
      "GET /api/v1/config/artifact-cache": json(
        anArtifactCache({ edit_epoch: "11111111111111111111111111111111" }),
      ),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review current values" }));
    expect(await screen.findByRole("button", { name: "Save revised changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use current values" })).toBeEnabled();
  });
  it("retires cache command feedback with its session", async () => {
    const response = Promise.withResolvers<Response>();
    const view = renderApp(<ArtifactCacheCard />, {
      routes: {
        "GET /api/v1/config/artifact-cache": json(INITIAL),
        "POST /api/v1/config/artifact-cache/clear": () => response.promise,
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Clear cached files" }));
    await act(async () => clearLogin());
    await act(async () => response.resolve(json(anArtifactCache({ usage: { bytes: 0 } }))));
    expect(screen.queryByText("Artifact cache settings updated.")).not.toBeInTheDocument();
    expect(
      view.client
        .getQueryCache()
        .getAll()
        .some((entry) => entry.state.data !== undefined),
    ).toBe(false);
  });
  it("hides denied cache controls", async () => {
    const view = renderApp(<ArtifactCacheCard />, {
      routes: { "GET /api/v1/config/artifact-cache": json(INITIAL) },
    });
    await screen.findByRole("button", { name: "Save cache settings" });
    view.route({ "GET /api/v1/config/artifact-cache": json({ detail: "denied" }, 403) });
    await act(async () => {
      await view.client.invalidateQueries();
    });
    expect(await screen.findByRole("button", { name: "Retry" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Save cache settings" })).not.toBeInTheDocument();
  });
});
