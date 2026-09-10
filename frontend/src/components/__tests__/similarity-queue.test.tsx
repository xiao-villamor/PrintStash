/** The queue distinguishes disabled, empty, stale and incomplete work while keeping review filters on the wire. */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { aCollection } from "@/test-support/factories";
import { SimilarityQueue } from "@/components/similarity-queue";
import { aSimilarityCandidate, aSimilarityRun, similarityStatus } from "@/test-support/similarity";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";

function renderQueue(options: RenderAppOptions = {}, modelId?: number) {
  return renderApp(<SimilarityQueue modelId={modelId} />, {
    ...options,
    routes: {
      "GET /api/v1/similarity/status": json(similarityStatus()),
      "GET /api/v1/collections": json([]),
      "GET /api/v1/similarity/candidates": json({ items: [], next_cursor: null }),
      ...options.routes,
    },
  });
}
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SimilarityQueue", () => {
  it("explains disabled analysis", async () => {
    renderQueue({
      routes: { "GET /api/v1/similarity/status": json(similarityStatus({ enabled: false })) },
    });
    expect(await screen.findByText("Similarity analysis is off")).toBeVisible();
  });
  it("identifies an empty review queue", async () => {
    renderQueue();
    expect(await screen.findByText("No candidates to review")).toBeVisible();
  });
  it("links a verified pair to its comparison", async () => {
    renderQueue({
      routes: {
        "GET /api/v1/similarity/candidates": json({
          items: [aSimilarityCandidate()],
          next_cursor: null,
        }),
      },
    });
    expect(await screen.findByRole("link", { name: "Compare" })).toHaveAttribute(
      "href",
      "/library/similar/1",
    );
    expect(screen.getByRole("link", { name: "Bracket copy" })).toHaveAttribute("href", "/models/2");
  });
  it("starts only the selected Model work", async () => {
    const user = userEvent.setup();
    const rendered = renderQueue(
      {
        routes: {
          "POST /api/v1/models/7/similar/query": json({
            items: [],
            next_cursor: null,
            run: aSimilarityRun({ id: 2 }),
          }),
          "GET /api/v1/similarity/runs/2": json(
            aSimilarityRun({
              id: 2,
              state: "completed",
              counters: { unsupported: 1, skipped_by_budget: 5 },
            }),
          ),
        },
      },
      7,
    );
    await user.click(await screen.findByRole("button", { name: "Find similar" }));
    await waitFor(() =>
      expect(rendered.requestsWithMethod("POST")).toMatchObject([
        { url: "/api/v1/models/7/similar/query", body: "{}" },
      ]),
    );
    expect(await screen.findByText(/Some candidates were left out/)).toBeVisible();
    expect(await screen.findByText("1 unsupported Artifact")).toBeVisible();
  });
  it("reports a failed result request", async () => {
    renderQueue({
      routes: { "GET /api/v1/similarity/candidates": json({ detail: "failed" }, 503) },
    });
    expect(await screen.findByText("Could not load similarity results")).toBeVisible();
  });
});

describe("Spanish review states", () => {
  it("explains an empty queue in Spanish", async () => {
    renderQueue({ locale: "es" });
    expect(await screen.findByText("No hay candidatos por revisar")).toBeVisible();
  });
  it("labels stale evidence in Spanish", async () => {
    renderQueue({
      locale: "es",
      routes: {
        "GET /api/v1/similarity/candidates": json({
          items: [aSimilarityCandidate({ freshness: "stale" })],
          next_cursor: null,
        }),
      },
    });
    expect(await screen.findByText("Evidencia desactualizada")).toBeVisible();
  });
});

describe("Queue navigation", () => {
  it.each([
    {
      label: "Review candidates",
      key: "review_state",
      value: "rejected",
      reset: "open",
    },
    {
      label: "All evidence classes",
      key: "evidence_class",
      value: "remeshed",
      reset: "",
    },
    { label: "Current evidence", key: "freshness", value: "stale", reset: "" },
    { label: "Collection", key: "collection_id", value: "8", reset: "" },
    { label: "All formats", key: "file_type", value: "3mf", reset: "" },
    { label: "All sources", key: "source", value: "external", reset: "" },
    { label: "All sources", key: "source", value: "vault", reset: "" },
  ])("sends the selected $key=$value filter", async ({ label, key, value, reset }) => {
    const user = userEvent.setup();
    const app = renderQueue({
      routes: {
        "GET /api/v1/collections": json([aCollection({ id: 8, path: "parts" })]),
        "GET /api/v1/similarity/candidates": (url) =>
          json({
            items:
              new URL(url, "http://localhost").searchParams.get(key) === value
                ? [aSimilarityCandidate()]
                : [],
            next_cursor: null,
          }),
      },
    });
    await screen.findByText("No candidates to review");
    await user.click(screen.getByText("Advanced settings"));
    await user.selectOptions(screen.getByRole("combobox", { name: label }), value);
    await waitFor(() =>
      expect(
        new URL(
          app
            .requestsWithMethod("GET")
            .filter((r) => r.url.includes("similarity/candidates"))
            .at(-1)!.url,
          "http://localhost",
        ).searchParams.get(key),
      ).toBe(value),
    );
    expect(await screen.findByRole("link", { name: "Compare" })).toBeVisible();
    await user.selectOptions(screen.getByRole("combobox", { name: label }), reset);
    expect(await screen.findByText("No candidates to review")).toBeVisible();
  });
  it("requests the next candidate page", async () => {
    const user = userEvent.setup();
    renderQueue({
      routes: {
        "GET /api/v1/similarity/candidates": (url) =>
          json(
            new URL(url, "http://localhost").searchParams.has("cursor")
              ? {
                  items: [
                    aSimilarityCandidate({
                      id: 2,
                      model_a: { id: 3, name: "Spatula", slug: "spatula", thumbnail_file_id: null },
                    }),
                  ],
                  next_cursor: null,
                }
              : { items: [aSimilarityCandidate()], next_cursor: "opaque-next" },
          ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("link", { name: "Spatula" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Bracket" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });
  it("retries a failed queue request", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderQueue({
      routes: {
        "GET /api/v1/similarity/candidates": () =>
          failed
            ? json({ detail: "unavailable" }, 503)
            : json({ items: [aSimilarityCandidate()], next_cursor: null }),
      },
    });
    await screen.findByText("Could not load similarity results");
    failed = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "Compare" })).toBeVisible();
  });
  it("filters known-good Revisions", async () => {
    const user = userEvent.setup();
    const app = renderQueue({
      routes: {
        "GET /api/v1/similarity/candidates": (url) =>
          json({
            items: new URL(url, "http://localhost").searchParams.has("known_good")
              ? [aSimilarityCandidate()]
              : [],
            next_cursor: null,
          }),
      },
    });
    await screen.findByText("No candidates to review");
    await user.click(screen.getByText("Advanced settings"));
    const toggle = screen.getByRole("checkbox", { name: "Has a known-good Revision" });
    await user.click(toggle);
    expect(await screen.findByRole("link", { name: "Compare" })).toBeVisible();
    expect(app.requestsWithMethod("GET").at(-1)?.url).toContain("known_good=true");
    await user.click(toggle);
    expect(await screen.findByText("No candidates to review")).toBeVisible();
  });
  it("applies the confidence threshold", async () => {
    const app = renderQueue();
    await screen.findByText("No candidates to review");
    fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence", hidden: true }), {
      target: { value: "75" },
    });
    await waitFor(() =>
      expect(app.requestsWithMethod("GET").at(-1)?.url).toContain("minimum_confidence=0.75"),
    );
  });
  it("reports partial embedding progress", async () => {
    const user = userEvent.setup();
    renderQueue(
      {
        routes: {
          "POST /api/v1/models/7/similar/query": json({
            items: [],
            next_cursor: null,
            run: aSimilarityRun({ id: 2 }),
          }),
          "GET /api/v1/similarity/runs/2": json(
            aSimilarityRun({
              id: 2,
              state: "completed",
              counters: { embedded: 3, artifacts_processed: 2, verified: 1 },
              checkpoint: { embedding_failure_code: "unavailable" },
            }),
          ),
        },
      },
      7,
    );
    await user.click(await screen.findByRole("button", { name: "Find similar" }));
    expect(await screen.findByText("3 visual vectors indexed")).toBeVisible();
    expect(screen.getByText(/Local embeddings were unavailable for this run/)).toBeVisible();
  });
});
