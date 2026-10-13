/** Administrator settings disclose inference capabilities and require independent consent. */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiSearchSettings } from "@/components/ai-search-settings";
import { aJob } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import {
  anInferenceEndpoint,
  anInferenceModel,
  aSearchGeneration,
  searchConfiguration,
  searchSettings,
  searchStatus,
} from "@/test-support/search";

async function settingsPanel(options: RenderAppOptions = {}) {
  const app = renderApp(<AiSearchSettings />, {
    ...options,
    routes: {
      "GET /api/v1/config/ai-search": json(
        searchConfiguration({
          settings: searchSettings({
            enabled: true,
            local_models_enabled: true,
            download_enabled: true,
          }),
          endpoints: [anInferenceEndpoint()],
        }),
      ),
      "GET /api/v1/config/ai-search/generations": json([aSearchGeneration()]),
      "GET /api/v1/inference/models": json([anInferenceModel()]),
      "GET /api/v1/jobs": json([]),
      "GET /api/v1/search/status": json(searchStatus({ semantic_ready: true })),
      "PUT /api/v1/config/ai-search": json(searchConfiguration({ edit_version: 2 })),
      ...options.routes,
    },
  });
  if (
    !options.routes?.["GET /api/v1/config/ai-search"] ||
    (options.routes["GET /api/v1/config/ai-search"] instanceof Response &&
      options.routes["GET /api/v1/config/ai-search"].ok)
  ) {
    await userEvent.click(
      await screen.findByRole("tab", { name: /Search types|Tipos de búsqueda/ }),
    );
  }
  return app;
}
afterEach(() => vi.unstubAllGlobals());

describe("AI Search settings", () => {
  it("keeps index tuning optional", async () => {
    await settingsPanel();
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    expect(screen.queryByRole("combobox", { name: "Index precision" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Customize index" }));
    expect(screen.getByRole("combobox", { name: "Index precision" })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Use automatically when ready" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Back to search choices" }));
    expect(screen.getByRole("button", { name: "Build new index" })).toBeVisible();
  }, 15_000);

  it("explains the first setup step when AI is off", async () => {
    await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search": json(
          searchConfiguration({ settings: searchSettings({ enabled: false }) }),
        ),
      },
    });
    expect(
      await screen.findByText("Open Technical, enable AI Search and save your settings first."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Build new index" })).toBeDisabled();
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    expect(
      screen.getByText("You choose each download below. Enabling this does not start a download."),
    ).toBeVisible();
  });

  it.each([
    {
      label: "local processing disabled",
      local: false,
      runtime: true,
      installed: true,
      message:
        "Open Technical, enable “Run AI on this machine” and save before using a local model.",
    },
    {
      label: "download required",
      local: true,
      runtime: true,
      installed: false,
      message: "Download the selected AI model before building the index.",
    },
    {
      label: "ready model",
      local: true,
      runtime: true,
      installed: true,
      message: "Ready to build. You can check the space needed first.",
    },
  ])("explains a $label", async ({ local, runtime, installed, message }) => {
    await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search": json(
          searchConfiguration({
            settings: searchSettings({
              enabled: true,
              local_models_enabled: local,
              download_enabled: true,
            }),
          }),
        ),
        "GET /api/v1/inference/models": json([
          anInferenceModel({ installed, runtime_available: runtime }),
        ]),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await userEvent.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(screen.getByText(message)).toBeVisible();
  });

  it("explains an index already building", async () => {
    await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search/generations": json([
          aSearchGeneration({ state: "building", phase: "backfill" }),
        ]),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await userEvent.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(
      screen.getByText(
        "An index for this search type is already building. Follow its progress below.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Build new index" })).toBeDisabled();
  });

  it("labels reconciliation without reporting vector progress", async () => {
    await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search/generations": json([
          aSearchGeneration({
            state: "building",
            phase: "reconcile",
            eligible: 124,
            indexed: 0,
          }),
        ]),
      },
    });
    expect(screen.getByText("Checking library content")).toBeVisible();
    expect(screen.queryByText("0 / 124 passages indexed")).toBeNull();
  });

  it("explains an estimate exceeding the storage budget", async () => {
    await settingsPanel({
      routes: {
        "POST /api/v1/config/ai-search/generations/estimate": json({
          passages: 24,
          estimated_bytes: 2097152,
          existing_bytes: 1048576,
          budget_bytes: 1048576,
          fits_budget: false,
          estimated_seconds: 120,
        }),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await userEvent.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    await userEvent.click(screen.getByRole("button", { name: "Estimate resources" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Build new index" })).toBeDisabled(),
    );
    expect(
      screen.queryByText("Ready to build. You can check the space needed first."),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByText(
        "This build exceeds the index storage budget. Increase the budget or remove an old index first.",
      )[0],
    ).toBeVisible();
  });

  it("explains missing download permission", async () => {
    await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search": json(
          searchConfiguration({
            settings: searchSettings({
              enabled: true,
              local_models_enabled: true,
              download_enabled: false,
            }),
          }),
        ),
        "GET /api/v1/inference/models": json([anInferenceModel({ installed: false })]),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await userEvent.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(
      screen.getByText(
        "Open Technical, allow AI model downloads and save. Then download this model.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Download model" })).toBeDisabled();
  });

  it("prepares the optional preplaced point profile", async () => {
    const user = userEvent.setup();
    const point = anInferenceModel({
      id: "e".repeat(64),
      key: "openshape-pointbert-vitb32-rgb",
      modality: "point_cloud",
      native_dimension: 512,
      curated: false,
    });
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/inference/models": json([anInferenceModel(), point]),
        "GET /api/v1/config/ai-search/generations": json([
          aSearchGeneration({ profile: "thumbnail", state: "active" }),
        ]),
        "POST /api/v1/config/ai-search/generations": json(
          aSearchGeneration({ profile: "point_cloud", model: point.key, state: "building" }),
        ),
      },
    });
    await user.click(await screen.findByRole("radio", { name: /Geometry search/ }));
    await user.click(await screen.findByRole("radio", { name: /openshape/ }));
    expect(screen.getByText(/Build a thumbnail or multiple-view index first/)).toBeVisible();
    expect(screen.queryByRole("radio", { name: "Best view" })).toBeNull();
    expect(screen.queryByRole("radio", { name: /bge-small/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Build new index" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      local_model_id: point.id,
      profile: "point_cloud",
      aggregation: "mean",
    });
  });
  it("builds a visual profile independently while a text generation is building", async () => {
    const user = userEvent.setup();
    const clip = anInferenceModel({
      id: "d".repeat(64),
      key: "clip-vit-base-patch32-fp32",
      modality: "text_image",
      native_dimension: 512,
    });
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/inference/models": json([anInferenceModel(), clip]),
        "GET /api/v1/config/ai-search/generations": json([
          aSearchGeneration({ state: "building", phase: "backfill" }),
        ]),
        "POST /api/v1/config/ai-search/generations": json(
          aSearchGeneration({ profile: "multiview", model: clip.key, state: "building" }),
        ),
      },
    });
    await user.click(await screen.findByRole("radio", { name: /multiple views/ }));
    await user.click(await screen.findByRole("radio", { name: /clip-vit/ }));
    expect(screen.queryByRole("radio", { name: /bge-small/ })).toBeNull();
    expect(screen.queryByRole("radio", { name: /server-encoder/ })).toBeNull();
    await user.click(screen.getByRole("radio", { name: "Best matching view" }));
    await user.click(screen.getByRole("button", { name: "Build new index" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      local_model_id: clip.id,
      profile: "multiview",
      aggregation: "max",
    });
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).not.toHaveProperty("query_prefix");
  });
  it("refreshes installed models when a download completes", async () => {
    const user = userEvent.setup();
    const job = aJob({ job_id: "download-2", kind: "inference.model_download", state: "running" });
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/inference/models": json([anInferenceModel({ installed: false })]),
        "GET /api/v1/jobs": json([job]),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await user.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(screen.getByRole("button", { name: "Build new index" })).toBeDisabled();
    app.route({
      "GET /api/v1/inference/models": json([anInferenceModel()]),
      "GET /api/v1/jobs": json([{ ...job, state: "completed" }]),
    });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "downloads"] });
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Build new index" })).toBeEnabled(),
    );
  });
  it("preserves the advanced draft on conflict", async () => {
    const app = await settingsPanel({
      routes: {
        "PUT /api/v1/config/ai-search": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    const weight = screen.getByRole("spinbutton", { name: "Keyword ranking weight" });
    fireEvent.change(weight, { target: { value: "2" } });
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    expect(await screen.findByRole("button", { name: "Review latest version" })).toBeVisible();
    expect(weight).toHaveValue(2);
    expect(screen.getByRole("button", { name: "Save search settings" })).toBeDisabled();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });

  it("saves deliberate edits after review", async () => {
    const app = await settingsPanel({
      routes: {
        "PUT /api/v1/config/ai-search": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Keyword ranking weight" }), {
      target: { value: "2" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    await screen.findByRole("button", { name: "Review latest version" });
    const latest = searchConfiguration({
      edit_version: 5,
      settings: searchSettings({
        enabled: true,
        local_models_enabled: true,
        download_enabled: true,
        timezone: "Europe/Madrid",
        semantic_weight: 3,
      }),
    });
    app.route({
      "GET /api/v1/config/ai-search": json(latest),
      "PUT /api/v1/config/ai-search": json({
        ...latest,
        edit_version: 6,
        settings: { ...latest.settings, lexical_weight: 2 },
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
    expect(await screen.findByText("Europe/Madrid")).toBeVisible();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    await userEvent.click(
      screen.getByRole("button", { name: "Save my draft against this version" }),
    );
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(2));
    expect(JSON.parse(app.requestsWithMethod("PUT")[1].body)).toEqual({
      ...latest.settings,
      lexical_weight: 2,
    });
    await waitFor(() =>
      expect(screen.getByRole("spinbutton", { name: "Semantic ranking weight" })).toHaveValue(3),
    );
  });

  it("requires review after an uncertain save", async () => {
    const app = await settingsPanel({
      routes: {
        "PUT /api/v1/config/ai-search": () => {
          throw new TypeError("Failed to fetch");
        },
      },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Keyword ranking weight" }), {
      target: { value: "2" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    expect(await screen.findByRole("button", { name: "Review latest version" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Save search settings" })).toBeDisabled();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });

  it.each([403, 404])("retires inaccessible review data after %s", async (status) => {
    const app = await settingsPanel({
      routes: { "PUT /api/v1/config/ai-search": json({ detail: "edit_conflict" }, 412) },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    await screen.findByRole("button", { name: "Review latest version" });
    app.route({ "GET /api/v1/config/ai-search": json({ detail: "forbidden" }, status) });
    await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
    await waitFor(() =>
      expect(screen.queryByRole("form", { name: "AI Search" })).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Save my draft against this version" }),
    ).not.toBeInTheDocument();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });

  it("preserves native validation on revised saves", async () => {
    const app = await settingsPanel({
      routes: { "PUT /api/v1/config/ai-search": json({ detail: "edit_conflict" }, 412) },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    await screen.findByRole("button", { name: "Review latest version" });
    app.route({ "GET /api/v1/config/ai-search": json(searchConfiguration({ edit_version: 5 })) });
    await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
    const save = await screen.findByRole("button", { name: "Save my draft against this version" });
    const weight = screen.getByRole("spinbutton", { name: "Keyword ranking weight" });
    fireEvent.change(weight, { target: { value: "11" } });
    await userEvent.click(save);
    expect(weight).toBeInvalid();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });

  it("keeps the draft editing base across a refetch", async () => {
    let submittedBase: string | null = null;
    const app = await settingsPanel({
      routes: {
        "PUT /api/v1/config/ai-search": (_url, init) => {
          submittedBase = new Headers(init?.headers).get("If-Match");
          return json({ detail: "edit_conflict" }, 412);
        },
      },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    const weight = screen.getByRole("spinbutton", { name: "Keyword ranking weight" });
    fireEvent.change(weight, { target: { value: "2" } });
    app.route({ "GET /api/v1/config/ai-search": json(searchConfiguration({ edit_version: 5 })) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "settings"] });
    });
    expect(weight).toHaveValue(2);
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    await screen.findByRole("button", { name: "Review latest version" });
    expect(submittedBase).toBe(`"search-settings-e${"a".repeat(32)}-v1"`);
  });

  it("requires adoption after the database history changes", async () => {
    const app = await settingsPanel({
      routes: { "PUT /api/v1/config/ai-search": json({ detail: "edit_conflict" }, 412) },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Technical" }));
    await userEvent.click(screen.getByRole("button", { name: "Save search settings" }));
    await screen.findByRole("button", { name: "Review latest version" });
    app.route({
      "GET /api/v1/config/ai-search": json(
        searchConfiguration({
          edit_epoch: "b".repeat(32),
          settings: searchSettings({ lexical_weight: 3 }),
        }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
    expect(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        "The database history changed. Adopt the current settings before starting a new edit.",
      ),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Use latest version" }));
    expect(screen.getByRole("spinbutton", { name: "Keyword ranking weight" })).toHaveValue(3);
    expect(screen.getByRole("button", { name: "Save search settings" })).toBeEnabled();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });

  it("saves advanced ranking choices explicitly", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel();
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Keyword search backend" }),
      "ranked_like",
    );
    const weight = screen.getByRole("spinbutton", { name: "Keyword ranking weight" });
    await user.clear(weight);
    await user.type(weight, "2");
    await user.click(screen.getByRole("button", { name: "Save search settings" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toMatchObject({
      lexical_backend: "ranked_like",
      lexical_weight: 2,
      semantic_weight: 1,
      rrf_k: 60,
    });
  });
  it("requires the caption capability prerequisites", async () => {
    const user = userEvent.setup();
    await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search": json(
          searchConfiguration({
            endpoints: [
              anInferenceEndpoint({ kind: "chat", supports_images: true, native_dimension: null }),
            ],
          }),
        ),
      },
    });
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    const captions = screen.getByRole("checkbox", { name: "Enable generated descriptions" });
    expect(captions).toBeDisabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "Chat and descriptions" }), "2");
    expect(captions).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", {
        name: "Allow rendered previews to be sent to the caption server",
      }),
    );
    expect(captions).toBeEnabled();
    await user.click(captions);
    await user.click(
      screen.getByRole("checkbox", {
        name: "Allow rendered previews to be sent to the caption server",
      }),
    );
    expect(captions).not.toBeChecked();
    expect(captions).toBeDisabled();
  });
  it("saves independent opt-ins without submitting endpoint secrets", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel();
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    await user.click(await screen.findByRole("checkbox", { name: "Enable AI Search" }));
    await user.click(screen.getByRole("button", { name: "Save search settings" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toMatchObject({
      enabled: false,
      local_models_enabled: true,
    });
    expect(app.requestsWithMethod("PUT")[0].body).not.toContain("api_key");
  });
  it("separates pending model selection from the serving index", async () => {
    const user = userEvent.setup();
    await settingsPanel();
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await user.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(screen.getByText("Search currently in use").parentElement).toHaveTextContent(
      "old-encoder",
    );
    expect(screen.getByText(/Your current search stays available/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Build new index" })).toBeEnabled();
  });
  it("shows pinned provenance with a current-library estimate", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel({
      routes: {
        "POST /api/v1/config/ai-search/generations/estimate": json({
          passages: 24,
          estimated_bytes: 2097152,
          existing_bytes: 1048576,
          budget_bytes: 2147483648,
          fits_budget: true,
          estimated_seconds: 120,
        }),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await user.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(screen.getByText("MIT")).toBeVisible();
    expect(screen.getByText("en")).toBeVisible();
    expect(screen.getByText(/BAAI\/bge-small-en-v1.5@5c38/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Estimate resources" }));
    expect(await screen.findByText(/24 passages/)).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    expect(app.requestsWithMethod("POST")[0].url).toContain("/estimate");
  });
  it("starts a new index without replacing the serving label", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel({
      routes: {
        "POST /api/v1/config/ai-search/generations": json(
          aSearchGeneration({ id: 2, state: "building", phase: "reconcile" }),
        ),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await user.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    await user.click(screen.getByRole("button", { name: "Build new index" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      local_model_id: "a".repeat(64),
      auto_activate: true,
      quantization: "float32",
    });
    expect(screen.getByText("Search currently in use").parentElement).toHaveTextContent(
      "old-encoder",
    );
  });
  it("requires an explicit download request", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/inference/models": json([anInferenceModel({ installed: false })]),
        "POST /api/v1/inference/models/bge-small-en-v1.5/download": json({ job_id: "download-1" }),
      },
    });
    await screen.findByRole("radio", { name: /bge-small-en-v1.5/ });
    await user.click(await screen.findByRole("radio", { name: /bge-small-en-v1.5/ }));
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Build new index" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Download model" }));
    await waitFor(() =>
      expect(app.requestsWithMethod("POST")[0]?.url).toBe(
        "/api/v1/inference/models/bge-small-en-v1.5/download",
      ),
    );
  });
  it("displays cancellable download progress", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/jobs": json([
          aJob({
            job_id: "download-1",
            kind: "inference.model_download",
            state: "running",
            progress: 42,
            processed: 42000,
            total: 100000,
          }),
        ]),
        "POST /api/v1/inference/models/downloads/download-1/cancel": json(null, 204),
      },
    });
    expect(
      await screen.findByRole("progressbar", { name: "Model download progress" }),
    ).toHaveAttribute("value", "42");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(app.requestsWithMethod("POST")[0]?.url).toContain("download-1/cancel"),
    );
  });
  it.each(["cancel", "activate", "retry"] as const)(
    "sends a version-fenced %s action",
    async (action) => {
      const generation = aSearchGeneration({
        state: "building",
        phase: action === "activate" ? "ready" : "backfill",
        quarantined: action === "retry" ? 1 : 0,
      });
      const user = userEvent.setup();
      const app = await settingsPanel({
        routes: {
          "GET /api/v1/config/ai-search/generations": json([generation]),
          [`POST /api/v1/config/ai-search/generations/1/${action}`]: json(generation),
        },
      });
      const label = { cancel: "Cancel", activate: "Activate index", retry: "Retry" }[action];
      await user.click(await screen.findByRole("button", { name: label }));
      await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
      expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toEqual({
        version_token: "a".repeat(32),
      });
    },
  );
  it("keeps a generation action bound to the version selected before refresh", async () => {
    const generation = aSearchGeneration({
      state: "building",
      phase: "backfill",
      version_token: "captured-version",
    });
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/config/ai-search/generations": json([generation]),
        "POST /api/v1/config/ai-search/generations/1/cancel": json(
          { detail: "search_generation_stale" },
          409,
        ),
      },
    });
    let resume!: () => void;
    const pending = new Promise<void>((resolve) => {
      resume = resolve;
    });
    const cancellation = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(() => pending);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cancellation).toHaveBeenCalled());
    app.route({
      "GET /api/v1/config/ai-search/generations": json([
        { ...generation, model: "Refreshed encoder", version_token: "new-version" },
      ]),
    });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "generations"] });
    });
    expect(await screen.findByText(/Refreshed encoder/)).toBeVisible();
    await act(async () => {
      resume();
      await pending;
    });
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toEqual({
      version_token: "captured-version",
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled());
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    cancellation.mockRestore();
  });
  it("keeps saved credentials out of an unrelated endpoint edit", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel({
      routes: {
        "POST /api/v1/config/ai-search/endpoints": json(
          anInferenceEndpoint({ id: 3, model: "replacement" }),
        ),
      },
    });
    await user.click(screen.getByRole("tab", { name: "AI servers" }));
    await user.click(screen.getByRole("button", { name: "Edit server-encoder" }));
    const form = screen.getByRole("form", { name: "Inference server" });
    expect(within(form).getByLabelText("API key (optional)")).toHaveValue("");
    expect(within(form).getByText(/Credentials are configured/)).toBeVisible();
    await user.clear(within(form).getByRole("textbox", { name: "Model" }));
    await user.type(within(form).getByRole("textbox", { name: "Model" }), "replacement");
    await user.click(within(form).getByRole("button", { name: "Test and save server" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    const proposal = JSON.parse(app.requestsWithMethod("POST")[0].body);
    expect(proposal).toMatchObject({ model: "replacement", inherit_credentials_from_id: 2 });
    expect(proposal).not.toHaveProperty("api_key");
    expect(proposal).not.toHaveProperty("headers");
  });
  it("requires explicit replacement of authentication headers", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel({
      routes: { "POST /api/v1/config/ai-search/endpoints": json(anInferenceEndpoint({ id: 3 })) },
    });
    await user.click(screen.getByRole("tab", { name: "AI servers" }));
    await user.click(screen.getByRole("button", { name: "Edit server-encoder" }));
    await user.click(screen.getByRole("checkbox", { name: "Replace saved headers" }));
    await user.click(screen.getByRole("button", { name: "Add header" }));
    await user.type(screen.getByLabelText("Header name 1"), "X-New-Key");
    await user.type(screen.getByLabelText("Header value 1"), "test-only-key");
    await user.click(screen.getByRole("button", { name: "Test and save server" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body).headers).toEqual({
      "X-New-Key": "test-only-key",
    });
  });
  it("supports Spanish maintenance controls", async () => {
    await settingsPanel({ locale: "es" });
    expect(screen.getByText("Crear o cambiar la búsqueda")).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Opciones técnicas" }));
    expect(
      await screen.findByRole("button", { name: "Guardar ajustes de búsqueda" }),
    ).toBeVisible();
  });
  it("keeps guided navigation available after a settings refresh fails", async () => {
    const app = await settingsPanel();
    app.route({ "GET /api/v1/config/ai-search": json({}, 503) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "settings"] });
    });
    expect(await screen.findByText("AI Search settings could not load")).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Guided setup" }));
    app.route({
      "GET /api/v1/config/ai-search": json(
        searchConfiguration({ settings: searchSettings({ enabled: false }) }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Enable local AI Search" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Prepare search by meaning" })).toBeVisible();
  });
  it("keeps a technical draft through background settings replacement", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel();
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    const weight = screen.getByRole("spinbutton", { name: "Keyword ranking weight" });
    await user.clear(weight);
    await user.type(weight, "2");
    app.route({
      "GET /api/v1/config/ai-search": json(
        searchConfiguration({
          settings: searchSettings({ enabled: false, lexical_weight: 3 }),
          endpoints: [
            anInferenceEndpoint({
              id: 7,
              kind: "chat",
              model: "refreshed-chat",
              native_dimension: null,
            }),
          ],
        }),
      ),
    });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "settings"] });
    });
    expect(await screen.findByRole("option", { name: /refreshed-chat/ })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Keyword ranking weight" })).toHaveValue(2);
    expect(screen.getByRole("checkbox", { name: "Enable AI Search" })).toBeChecked();
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });
  it("keeps a failed-refresh technical draft read-only until retry", async () => {
    const user = userEvent.setup();
    const app = await settingsPanel();
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    const weight = screen.getByRole("spinbutton", { name: "Keyword ranking weight" });
    await user.clear(weight);
    await user.type(weight, "2");
    app.route({ "GET /api/v1/config/ai-search": json({}, 503) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "settings"] });
    });
    expect(await screen.findByText("AI Search settings could not load")).toBeVisible();
    expect(screen.getByRole("spinbutton", { name: "Keyword ranking weight" })).toHaveValue(2);
    expect(screen.getByRole("button", { name: "Save search settings" })).toBeDisabled();
    app.route({ "GET /api/v1/config/ai-search": json(searchConfiguration()) });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save search settings" })).toBeEnabled(),
    );
    expect(screen.getByRole("spinbutton", { name: "Keyword ranking weight" })).toHaveValue(2);
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });
  it("reports a settings load failure with retry", async () => {
    await settingsPanel({ routes: { "GET /api/v1/config/ai-search": json({}, 503) } });
    expect(await screen.findByText("AI Search settings could not load")).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});

describe("Sparse expansion settings", () => {
  it("saves independent local expansion consent", async () => {
    const user = userEvent.setup();
    const sparse = anInferenceModel({
      id: "4".repeat(64),
      key: "splade-pp-en-v1",
      modality: "sparse",
      native_dimension: 30522,
    });
    const app = await settingsPanel({
      routes: { "GET /api/v1/inference/models": json([anInferenceModel(), sparse]) },
    });
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Expansion model" }), sparse.id);
    await user.click(screen.getByRole("checkbox", { name: "Enable lexical expansion" }));
    await user.click(screen.getByRole("button", { name: "Save search settings" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toMatchObject({
      sparse_model_id: sparse.id,
      sparse_expansion_enabled: true,
    });
    expect(screen.queryByRole("radio", { name: /splade/ })).toBeNull();
  });

  it("requires an installed sparse model before enabling expansion", async () => {
    const user = userEvent.setup();
    const sparse = anInferenceModel({
      id: "4".repeat(64),
      key: "splade-pp-en-v1",
      modality: "sparse",
      installed: false,
    });
    const app = await settingsPanel({
      routes: {
        "GET /api/v1/inference/models": json([anInferenceModel(), sparse]),
        "POST /api/v1/inference/models/splade-pp-en-v1/download": json({
          job_id: "sparse-download",
        }),
      },
    });
    await user.click(screen.getByRole("tab", { name: "Technical" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Expansion model" }), sparse.id);
    expect(screen.getByRole("checkbox", { name: "Enable lexical expansion" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Download model" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(app.requestsWithMethod("POST")[0].url).toContain(
      "/inference/models/splade-pp-en-v1/download",
    );
  });
});
