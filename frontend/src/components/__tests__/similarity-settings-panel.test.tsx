/** Similar Models owns opt-in, bounded scan configuration and cooperative cancellation. */
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SimilaritySettingsPanel } from "@/components/similarity-settings-panel";
import { aSimilarityRun, similaritySettings, similarityStatus } from "@/test-support/similarity";
import { aCollection, aModel, anExternalLibrary } from "@/test-support/factories";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import { listTasks, resetTasksForNewSetup, trackSimilarityRun } from "@/lib/task-center";

function renderSettings(options: RenderAppOptions = {}) {
  return renderApp(<SimilaritySettingsPanel />, {
    ...options,
    routes: {
      "GET /api/v1/similarity/status": json(similarityStatus()),
      "GET /api/v1/similarity/runs": json({ items: [], next_cursor: null }),
      ...collectionTreeRoutes([aCollection({ id: 5, name: "Parts", path: "parts" })]),
      "GET /api/v1/libraries": json([]),
      "POST /api/v1/similarity/selection-preview": json({ total: 0, by_class: {} }),
      "PATCH /api/v1/similarity/settings": json(similaritySettings({ enabled: false })),
      ...options.routes,
    },
  });
}
afterEach(() => {
  vi.unstubAllGlobals();
  resetTasksForNewSetup();
});

describe("SimilaritySettingsPanel", () => {
  it("publishes acknowledged cancellation before a failed history refresh", async () => {
    let cancelled = false;
    renderSettings({
      routes: {
        "GET /api/v1/similarity/runs": () =>
          cancelled
            ? json({ detail: "unavailable" }, 503)
            : json({ items: [aSimilarityRun({ state: "running" })], next_cursor: null }),
        "POST /api/v1/similarity/runs/1/cancel": () => {
          cancelled = true;
          return json(aSimilarityRun({ state: "cancelling" }));
        },
      },
    });
    await userEvent.click(screen.getByText("Analysis history"));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel analysis" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Cancel analysis" })).toBeDisabled();
    expect(screen.getByText("Cancelling", { exact: true })).toBeVisible();
  });
  it("retains an unavailable selected source without dispatching it", async () => {
    const app = renderSettings({
      routes: { "GET /api/v1/libraries": json([anExternalLibrary({ id: 7, name: "Workshop" })]) },
    });
    await screen.findByRole("option", { name: /Workshop/ });
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Analysis scope" }),
      "source:7",
    );
    app.route({ "GET /api/v1/libraries": json({ detail: "feature_disabled" }, 404) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["similarity", "sources"] });
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled(),
    );
    expect(screen.getByRole("combobox", { name: "Analysis scope" })).toHaveValue("source:7");
    expect(screen.getByRole("option", { name: /Workshop/ })).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Analysis scope" }),
      "library",
    );
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
    expect(
      app.requestsWithMethod("POST").filter((request) => request.url.endsWith("/runs")),
    ).toEqual([]);
  });
  it("keeps library analysis available when external sources are disabled", async () => {
    const app = renderSettings({
      routes: { "GET /api/v1/libraries": json({ detail: "feature_disabled" }, 404) },
    });
    await waitFor(() =>
      expect(app.client.getQueryState(["similarity", "sources"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
    expect(
      screen.getAllByRole("option", { hidden: true }).map((option) => option.textContent),
    ).toContain("Entire library");
  });
  it("retries unavailable Model choices", async () => {
    const app = renderSettings({
      routes: { "GET /api/v1/models": json({ detail: "unavailable" }, 503) },
    });
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Analysis scope" }),
      "models",
    );
    await screen.findByRole("alert");
    expect(screen.queryByText("No Models match this search")).not.toBeInTheDocument();
    app.route({ "GET /api/v1/models": json([aModel({ name: "Recovered choice" })]) });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("checkbox", { name: "Recovered choice" })).toBeEnabled();
  });
  it("retries a failed candidate-count preview", async () => {
    const app = renderSettings({
      routes: { "POST /api/v1/similarity/selection-preview": json({ detail: "unavailable" }, 503) },
    });
    await userEvent.click(screen.getByText("Analysis options"));
    await screen.findByRole("alert");
    app.route({ "POST /api/v1/similarity/selection-preview": json({ total: 3, by_class: {} }) });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/3 open candidates meet these thresholds/)).toBeVisible();
    expect(app.requestsWithMethod("POST").map((request) => request.url)).toEqual([
      "/api/v1/similarity/selection-preview",
      "/api/v1/similarity/selection-preview",
    ]);
  });
  it("keeps a dirty budget through failed background reads", async () => {
    const app = renderSettings();
    await userEvent.click(screen.getByText("Analysis options"));
    await userEvent.click(
      await screen.findByText("Advanced settings", { selector: "form summary" }),
    );
    const budget = screen.getByRole("spinbutton", { name: "Triangle limit per mesh" });
    await userEvent.clear(budget);
    await userEvent.type(budget, "150000");
    app.route({ "GET /api/v1/similarity/status": json({ detail: "unavailable" }, 503) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["similarity", "status"] });
    });
    await screen.findByText("Could not load similarity results");
    expect(budget).toHaveValue(150000);
    expect(budget).toBeDisabled();
    app.route({ "GET /api/v1/similarity/status": json(similarityStatus()) });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(budget).toBeEnabled());
    expect(budget).toHaveValue(150000);
  });
  it.each([
    { resource: "history", endpoint: "GET /api/v1/similarity/runs", open: "Analysis history" },
    { resource: "sources", endpoint: "GET /api/v1/libraries", open: null },
  ])("retries an unavailable $resource projection", async ({ endpoint, open }) => {
    const app = renderSettings({ routes: { [endpoint]: json({ detail: "unavailable" }, 503) } });
    if (open) await userEvent.click(screen.getByText(open));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load similarity results");
    const reads = () =>
      app.requestsWithMethod("GET").filter((request) => request.url === endpoint.slice(4));
    const before = reads().length;
    app.route({
      [endpoint]: json(endpoint.endsWith("runs") ? { items: [], next_cursor: null } : []),
    });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(reads()).toHaveLength(before + 1));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
  it("adopts the acknowledged budget without a second settings write", async () => {
    const app = renderSettings({
      routes: {
        "PATCH /api/v1/similarity/settings": json(similaritySettings({ triangle_cap: 123456 })),
      },
    });
    await userEvent.click(screen.getByText("Analysis options"));
    await userEvent.click(
      await screen.findByText("Advanced settings", { selector: "form summary" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(screen.getByRole("spinbutton", { name: "Triangle limit per mesh" })).toHaveValue(
        123456,
      ),
    );
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it("preserves a dirty budget during status refresh", async () => {
    const app = renderSettings();
    await userEvent.click(screen.getByText("Analysis options"));
    await userEvent.click(
      await screen.findByText("Advanced settings", { selector: "form summary" }),
    );
    const budget = screen.getByRole("spinbutton", { name: "Triangle limit per mesh" });
    await userEvent.clear(budget);
    await userEvent.type(budget, "150000");
    app.route({
      "GET /api/v1/similarity/status": json(
        similarityStatus({
          settings: similaritySettings({ triangle_cap: 180000 }),
          capabilities: { multipart_resolution: true, step: false, local_embeddings: false },
        }),
      ),
    });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["similarity", "status"] });
    });
    await screen.findByText("STEP requires a full installation");
    expect(screen.getByRole("spinbutton", { name: "Triangle limit per mesh" })).toHaveValue(150000);
  });
  it("saves a complete dense-mesh budget", async () => {
    const user = userEvent.setup();
    const rendered = renderSettings();
    await user.click(screen.getByText("Analysis options"));
    await user.click(await screen.findByText("Advanced settings", { selector: "form summary" }));
    const input = screen.getByRole("spinbutton", { name: "Triangle limit per mesh" });
    await user.clear(input);
    await user.type(input, "2000000");
    expect(input).toBeValid();

    await user.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(rendered.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(rendered.requestsWithMethod("PATCH")[0].body)).toMatchObject({
      triangle_cap: 2000000,
    });
  });

  it("persists the operator opt-in", async () => {
    const user = userEvent.setup();
    const rendered = renderSettings();
    await user.click(screen.getByText("Analysis options"));
    await user.click(await screen.findByRole("checkbox", { name: "Enable similarity analysis" }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(rendered.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(rendered.requestsWithMethod("PATCH")[0].body)).toMatchObject({
      enabled: false,
      sample_points: 5000,
      max_candidates: 20,
    });
  });
  it("requests cancellation for a running scan", async () => {
    const user = userEvent.setup();
    const rendered = renderSettings({
      routes: {
        "GET /api/v1/similarity/runs": json({
          items: [aSimilarityRun({ state: "running" })],
          next_cursor: null,
        }),
        "POST /api/v1/similarity/runs/1/cancel": json(aSimilarityRun({ state: "cancelling" })),
      },
    });
    await user.click(screen.getByText("Analysis history"));
    await user.click(await screen.findByRole("button", { name: "Cancel analysis" }));
    await waitFor(() =>
      expect(
        rendered.requestsWithMethod("POST").filter((request) => request.url.endsWith("/cancel"))[0]
          ?.url,
      ).toBe("/api/v1/similarity/runs/1/cancel"),
    );
  });
  it("previews thresholds without starting a run", async () => {
    const user = userEvent.setup();
    const app = renderSettings({
      routes: {
        "POST /api/v1/similarity/selection-preview": json({
          total: 8,
          by_class: { identical_geometry: 8 },
        }),
      },
    });
    await user.click(screen.getByText("Analysis options"));
    expect(await screen.findByText(/8 open candidates meet these thresholds/)).toBeVisible();
    await user.click(screen.getByText("Advanced settings", { selector: "form summary" }));
    await user.click(screen.getByRole("checkbox", { name: "Set confidence per evidence class" }));
    const input = screen.getByRole("spinbutton", { name: /Identical geometry/ });
    await user.clear(input);
    await user.type(input, "99");
    await waitFor(() =>
      expect(JSON.parse(app.requestsWithMethod("POST").at(-1)!.body).class_overrides).toMatchObject(
        { identical_geometry: 0.99 },
      ),
    );
    expect(
      app.requestsWithMethod("POST").every((request) => request.url.endsWith("/selection-preview")),
    ).toBe(true);
  });
});

describe("Scoped analysis", () => {
  it("adds a started analysis to Tasks", async () => {
    const user = userEvent.setup();
    renderSettings({ routes: { "POST /api/v1/similarity/runs": json(aSimilarityRun()) } });

    await user.click(screen.getByRole("button", { name: "Start analysis" }));

    await waitFor(() => expect(listTasks()).toMatchObject([{ similarityRunId: 1 }]));
  });

  it("prevents a second analysis for the active scope", async () => {
    trackSimilarityRun(aSimilarityRun());
    const app = renderSettings({
      routes: {
        "GET /api/v1/similarity/runs": json({ items: [aSimilarityRun()], next_cursor: null }),
      },
    });

    expect(await screen.findByText("An analysis for this scope is already running.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
    expect(
      app.requestsWithMethod("POST").filter((request) => request.url.endsWith("/runs")),
    ).toHaveLength(0);
  });

  it("allows an administrator to start alongside another user's run", async () => {
    renderSettings({
      routes: {
        "GET /api/v1/similarity/runs": json({ items: [aSimilarityRun()], next_cursor: null }),
      },
    });

    await screen.findByText("Queued");

    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
  });

  it("explains a conflict when another browser starts the same scope first", async () => {
    const user = userEvent.setup();
    renderSettings({
      routes: {
        "POST /api/v1/similarity/runs": json({ detail: "similarity_run_active" }, 409),
      },
    });

    await user.click(screen.getByRole("button", { name: "Start analysis" }));

    expect(
      await screen.findByText(
        "An analysis for this scope is already running. Check Analysis history.",
      ),
    ).toBeVisible();
  });

  it("permits a different scope while a library analysis runs", async () => {
    const user = userEvent.setup();
    trackSimilarityRun(aSimilarityRun());
    renderSettings({
      routes: {
        "GET /api/v1/similarity/runs": json({ items: [aSimilarityRun()], next_cursor: null }),
        "GET /api/v1/libraries": json([anExternalLibrary({ id: 7, name: "Workshop" })]),
      },
    });

    await screen.findByText("An analysis for this scope is already running.");
    await user.selectOptions(screen.getByRole("combobox", { name: "Analysis scope" }), "source:7");

    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
  });
  it("starts a selected external source", async () => {
    const user = userEvent.setup();
    const app = renderSettings({
      routes: {
        "GET /api/v1/libraries": json([anExternalLibrary({ id: 7, name: "Workshop" })]),
        "POST /api/v1/similarity/runs": json(aSimilarityRun()),
      },
    });
    await screen.findByRole("option", { name: /Workshop/ });
    await user.selectOptions(screen.getByRole("combobox", { name: "Analysis scope" }), "source:7");
    await user.click(screen.getByRole("button", { name: "Start analysis" }));
    await waitFor(() =>
      expect(
        app.requestsWithMethod("POST").find((request) => request.url.endsWith("/runs"))?.body,
      ).toBe(JSON.stringify({ scope: "sources", ids: [7] })),
    );
  });

  it("starts a selected collection without loading the whole tree", async () => {
    const user = userEvent.setup();
    const app = renderSettings({
      routes: { "POST /api/v1/similarity/runs": json(aSimilarityRun()) },
    });
    const scope = await screen.findByRole("combobox", { name: "Analysis scope" });

    await user.selectOptions(scope, "pick");
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
    await user.click(await screen.findByRole("option", { name: /Parts/ }));
    await user.click(screen.getByRole("button", { name: "Start analysis" }));

    await waitFor(() =>
      expect(
        app.requestsWithMethod("POST").find((request) => request.url.endsWith("/runs"))?.body,
      ).toBe(JSON.stringify({ scope: "collections", ids: [5] })),
    );
    expect(
      app
        .requests()
        .filter(
          (request) => new URL(request.url, "http://test").pathname === "/api/v1/collections",
        ),
    ).toEqual([]);
  });
  it("starts only checked Models", async () => {
    const user = userEvent.setup();
    const app = renderSettings({
      routes: {
        "GET /api/v1/models": json([
          aModel({ id: 7, name: "Bracket" }),
          aModel({ id: 8, name: "Gear" }),
        ]),
        "POST /api/v1/similarity/runs": json(aSimilarityRun()),
      },
    });
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Analysis scope" }),
      "models",
    );
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
    await user.click(await screen.findByRole("checkbox", { name: "Gear" }));
    await user.click(screen.getByRole("button", { name: "Start analysis" }));
    await waitFor(() =>
      expect(
        app.requestsWithMethod("POST").find((request) => request.url.endsWith("/runs"))?.body,
      ).toBe(JSON.stringify({ scope: "models", ids: [8] })),
    );
  });
  it("clears a Model selection", async () => {
    const user = userEvent.setup();
    renderSettings({ routes: { "GET /api/v1/models": json([aModel({ name: "Bracket" })]) } });
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Analysis scope" }),
      "models",
    );
    await user.click(await screen.findByRole("checkbox", { name: "Bracket" }));
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.getByRole("checkbox", { name: "Bracket" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
  });
  it("explains failed preview counts", async () => {
    const user = userEvent.setup();
    renderSettings({
      routes: { "POST /api/v1/similarity/selection-preview": json({ detail: "unavailable" }, 503) },
    });
    await user.click(screen.getByText("Analysis options"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not update the candidate count",
    );
    expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled();
  });
});

describe("Localized analysis outcomes", () => {
  it.each([
    {
      locale: "en",
      count: 1,
      partial: "1 partial Artifact",
      unsupported: "1 unsupported Artifact",
    },
    {
      locale: "en",
      count: 2,
      partial: "2 partial Artifacts",
      unsupported: "2 unsupported Artifacts",
    },
    {
      locale: "es",
      count: 1,
      partial: "1 Artefacto parcial",
      unsupported: "1 Artefacto no compatible",
    },
    {
      locale: "es",
      count: 2,
      partial: "2 Artefactos parciales",
      unsupported: "2 Artefactos no compatibles",
    },
  ] as const)(
    "renders $locale outcomes for $count",
    async ({ locale, count, partial, unsupported }) => {
      renderSettings({
        locale,
        routes: {
          "GET /api/v1/similarity/runs": json({
            items: [
              aSimilarityRun({
                state: "completed",
                counters: { partial: count, unsupported: count },
              }),
            ],
            next_cursor: null,
          }),
        },
      });
      await userEvent.click(
        screen.getByText(locale === "es" ? "Historial de análisis" : "Analysis history"),
      );
      expect(await screen.findByText(partial)).toBeVisible();
      expect(await screen.findByText(unsupported)).toBeVisible();
    },
  );
});
