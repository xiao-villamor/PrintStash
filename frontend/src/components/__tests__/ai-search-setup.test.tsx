/** Guided AI setup keeps consent explicit and advances only after successful server operations. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AiSearchSettings } from "@/components/ai-search-settings";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import {
  anInferenceEndpoint,
  anInferenceModel,
  aSearchGeneration,
  searchConfiguration,
  searchSettings,
  searchStatus,
} from "@/test-support/search";
import { anIngestJob } from "@/test-support/factories";
const enabled = searchSettings({ enabled: true, local_models_enabled: true });
function setup(options: RenderAppOptions = {}) {
  return renderApp(<AiSearchSettings />, {
    ...options,
    routes: {
      "GET /api/v1/config/ai-search": json(searchConfiguration()),
      "GET /api/v1/inference/models": json([anInferenceModel()]),
      "GET /api/v1/config/ai-search/generations": json([]),
      "GET /api/v1/ingest/jobs": json([]),
      "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
      ...options.routes,
    },
  });
}
describe("AiSearchSetup", () => {
  it("starts with a processing location choice", async () => {
    const app = setup();
    await screen.findByText("Where should AI Search run?");
    expect(screen.getByRole("heading", { name: "Where should AI Search run?" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Use this machine" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Connect another server" })).toBeVisible();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("enables local processing only on request", async () => {
    const app = setup({
      routes: { "PUT /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })) },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Use this machine" }));
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    app.route({ "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })) });
    await userEvent.click(screen.getByRole("button", { name: "Enable local AI Search" }));
    expect(await screen.findByRole("button", { name: "Prepare my library" })).toBeVisible();
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toEqual(enabled);
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("requires explicit download consent", async () => {
    const model = anInferenceModel({ installed: false });
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/inference/models": json([model]),
        "PUT /api/v1/config/ai-search": json(
          searchConfiguration({ settings: { ...enabled, download_enabled: true } }),
        ),
        "POST /api/v1/inference/models/": json({ job_id: "download-1" }),
      },
    });
    const button = await screen.findByRole("button", { name: "Allow download and continue" });
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    app.route({
      "GET /api/v1/ingest/jobs": json([
        anIngestJob({ job_id: "download-1", kind: "model_download", state: "running" }),
      ]),
    });
    await userEvent.click(button);
    expect(await screen.findByText("Downloading an AI model")).toBeVisible();
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toEqual({
      ...enabled,
      download_enabled: true,
    });
    expect(app.requestsWithMethod("POST")[0].url).toContain(`/models/${model.key}/download`);
  });
  it("prepares a library using safe defaults", async () => {
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "POST /api/v1/config/ai-search/generations/estimate": json({
          passages: 10,
          estimated_bytes: 1000,
          existing_bytes: 0,
          budget_bytes: 2000,
          fits_budget: true,
          estimated_seconds: 10,
        }),
        "POST /api/v1/config/ai-search/generations": json(aSearchGeneration({ state: "building" })),
      },
    });
    const button = await screen.findByRole("button", { name: "Prepare my library" });
    app.route({
      "GET /api/v1/config/ai-search/generations": json([
        aSearchGeneration({ state: "building", phase: "backfill" }),
      ]),
    });
    await userEvent.click(button);
    expect(await screen.findByRole("heading", { name: "Preparing your library" })).toBeVisible();
    const posts = app.requestsWithMethod("POST");
    expect(posts.map((post) => post.url)).toEqual([
      "/api/v1/config/ai-search/generations/estimate",
      "/api/v1/config/ai-search/generations",
    ]);
    expect(JSON.parse(posts[1].body)).toEqual({
      local_model_id: anInferenceModel().id,
      profile: "semantic_text",
      index_backend: "auto",
      quantization: "float32",
      auto_activate: true,
    });
  });
  it("blocks preparation over budget", async () => {
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "POST /api/v1/config/ai-search/generations/estimate": json({
          passages: 10,
          estimated_bytes: 3000,
          existing_bytes: 0,
          budget_bytes: 2000,
          fits_budget: false,
          estimated_seconds: 10,
        }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Prepare my library" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not enough space");
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it.each([
    { label: "missing runtime", models: [anInferenceModel({ runtime_available: false })] },
    { label: "empty catalog", models: [] },
  ])("explains $label", async ({ models }) => {
    setup({ routes: { "GET /api/v1/inference/models": json(models) } });
    await userEvent.click(await screen.findByRole("button", { name: "Use this machine" }));
    expect(screen.getByText("Local AI is not available on this installation")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Enable local AI Search" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use another server" })).toBeVisible();
  });
  it.each([
    {
      label: "enable",
      initial: searchSettings(),
      installed: true,
      action: "Enable local AI Search",
      route: "PUT /api/v1/config/ai-search",
    },
    {
      label: "download",
      initial: { ...enabled, download_enabled: true },
      installed: false,
      action: "Allow download and continue",
      route: "POST /api/v1/inference/models/",
    },
    {
      label: "estimate",
      initial: enabled,
      installed: true,
      action: "Prepare my library",
      route: "POST /api/v1/config/ai-search/generations/estimate",
    },
    {
      label: "prepare",
      initial: enabled,
      installed: true,
      action: "Prepare my library",
      route: "POST /api/v1/config/ai-search/generations",
    },
  ])("recovers from a failed $label step", async ({ initial, installed, action, route }) => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: initial })),
        "GET /api/v1/inference/models": json([anInferenceModel({ installed })]),
        "POST /api/v1/config/ai-search/generations/estimate": json({
          passages: 10,
          estimated_bytes: 1000,
          existing_bytes: 0,
          budget_bytes: 2000,
          fits_budget: true,
          estimated_seconds: 10,
        }),
        [route]: json({ detail: "unavailable" }, 503),
      },
    });
    if (!initial.enabled)
      await userEvent.click(await screen.findByRole("button", { name: "Use this machine" }));
    await userEvent.click(await screen.findByRole("button", { name: action }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This step could not be completed");
    expect(screen.getByRole("button", { name: action })).toBeEnabled();
    expect(screen.queryByText("AI Search is ready")).not.toBeInTheDocument();
  });
  it("shows preparation progress", async () => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search/generations": json([
          aSearchGeneration({ state: "building", phase: "backfill", eligible: 10, indexed: 4 }),
        ]),
      },
    });
    expect(
      await screen.findByRole("progressbar", { name: "Library preparation progress" }),
    ).toHaveAttribute("value", "4");
    expect(screen.getByText("4 of 10 searchable entries prepared")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Prepare my library" })).not.toBeInTheDocument();
  });
  it("offers search when ready", async () => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/config/ai-search/generations": json([aSearchGeneration({ state: "active" })]),
      },
    });
    expect(await screen.findByRole("link", { name: "Try AI Search" })).toHaveAttribute(
      "href",
      "/search?q=a%20holder%20for%20my%20tools",
    );
    expect(screen.queryByRole("button", { name: "Prepare my library" })).not.toBeInTheDocument();
  });
  it("resumes an existing setup", async () => {
    setup({
      routes: { "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })) },
    });
    expect(await screen.findByRole("button", { name: "Prepare my library" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Enable local AI Search" }),
    ).not.toBeInTheDocument();
  });
  it("uses a configured server without local consent", async () => {
    const config = searchConfiguration({ endpoints: [anInferenceEndpoint()] });
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search": json(config),
        "PUT /api/v1/config/ai-search": json({
          ...config,
          settings: searchSettings({ enabled: true }),
        }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Connect another server" }));
    expect(
      screen.getByText("Library text and search queries will be sent to inference.local."),
    ).toBeVisible();
    app.route({
      "GET /api/v1/config/ai-search": json({
        ...config,
        settings: searchSettings({ enabled: true }),
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Enable server AI Search" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toEqual(
      searchSettings({ enabled: true }),
    );
  });
  it("exposes server connection only in its path", async () => {
    setup();
    await screen.findByRole("heading", { name: "Where should AI Search run?" });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Connect another server" }));
    expect(screen.getByRole("form", { name: "Inference server" })).toBeVisible();
  });
  it("preserves advanced AI controls", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "Advanced AI controls" }));
    expect(await screen.findByRole("checkbox", { name: "Enable AI Search" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Back to guided setup" }));
    expect(
      await screen.findByRole("heading", { name: "Where should AI Search run?" }),
    ).toBeVisible();
  });
  it("retries a failed setup read", async () => {
    const app = setup({
      routes: { "GET /api/v1/inference/models": json({ detail: "unavailable" }, 503) },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("AI setup could not be loaded");
    app.route({ "GET /api/v1/inference/models": json([anInferenceModel()]) });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Use this machine" })).toBeVisible();
  });
  it("cancels preparation with its current version", async () => {
    const generation = aSearchGeneration({
      state: "building",
      phase: "backfill",
      version_token: "version-1",
    });
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search/generations": json([generation]),
        "POST /api/v1/config/ai-search/generations/1/cancel": json({
          ...generation,
          state: "cancelled",
        }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel preparation" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toEqual({
      version_token: "version-1",
    });
  });
  it("activates an already prepared search", async () => {
    const generation = aSearchGeneration({ state: "building", phase: "ready" });
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search/generations": json([generation]),
        "POST /api/v1/config/ai-search/generations/1/activate": json({
          ...generation,
          state: "active",
        }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Use prepared search" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(app.requestsWithMethod("POST")[0].url).toContain("/activate");
  });
  it("cancels an active model download", async () => {
    const app = setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/ingest/jobs": json([
          anIngestJob({ job_id: "download-1", kind: "model_download", state: "running" }),
        ]),
        "POST /api/v1/inference/models/downloads/download-1/cancel": json({}, 200),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel download" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(app.requestsWithMethod("POST")[0].url).toContain("/downloads/download-1/cancel");
  });
  it("lets an existing user change setup", async () => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/config/ai-search/generations": json([aSearchGeneration({ state: "active" })]),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Change setup" }));
    expect(screen.getByRole("button", { name: "Prepare my library" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Change location" }));
    expect(screen.getByRole("button", { name: "Connect another server" })).toBeVisible();
  });
  it("explains a failed background preparation", async () => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/config/ai-search/generations": json([aSearchGeneration({ state: "failed" })]),
      },
    });
    expect(await screen.findByText(/Preparation stopped before search was ready/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Prepare my library" })).toBeEnabled();
  });
  it("discloses files missing from ready search", async () => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/config/ai-search/generations": json([
          aSearchGeneration({ state: "active", quarantined: 2 }),
        ]),
      },
    });
    expect(await screen.findByText(/Some files could not be prepared/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Try AI Search" })).toBeVisible();
  });
  it("does not call unavailable saved search ready", async () => {
    setup({
      routes: {
        "GET /api/v1/config/ai-search": json(searchConfiguration({ settings: enabled })),
        "GET /api/v1/config/ai-search/generations": json([aSearchGeneration({ state: "active" })]),
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: false })),
      },
    });
    expect(await screen.findByText(/Your saved search is not available right now/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "Try AI Search" })).not.toBeInTheDocument();
  });
});
