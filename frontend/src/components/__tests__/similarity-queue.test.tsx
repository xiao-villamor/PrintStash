/** The queue distinguishes disabled, empty, stale and incomplete work while keeping review filters on the wire. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
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
