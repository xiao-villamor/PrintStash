/** Review states and filters stay discoverable, accurate and restorable across comparison navigation. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { useLocation } from "@tanstack/react-router";
import { useRouter } from "@/lib/navigation";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { aCollection } from "@/test-support/factories";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { SimilarityQueue } from "@/components/similarity-queue";
import { aSimilarityCandidate, aSimilarityRun, similarityStatus } from "@/test-support/similarity";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";

function QueueNavigation() {
  const location = useLocation();
  const navigation = useRouter();
  return (
    <>
      <output aria-label="Current queue location">{location.pathname + location.searchStr}</output>
      <button
        onClick={() =>
          navigation.push("/library/similar?review=rejected&confidence=80&collection=9")
        }
      >
        Open another queue
      </button>
      <button onClick={() => navigation.back()}>Previous queue</button>
    </>
  );
}

function renderQueue(options: RenderAppOptions = {}, modelId?: number) {
  return renderApp(
    <>
      <SimilarityQueue modelId={modelId} />
      <QueueNavigation />
    </>,
    {
      ...options,
      routes: {
        "GET /api/v1/similarity/status": json(similarityStatus()),
        ...collectionTreeRoutes([aCollection({ id: 8, name: "Parts", path: "parts" })]),
        "GET /api/v1/similarity/candidates": json({ items: [], next_cursor: null }),
        ...options.routes,
      },
    },
  );
}
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SimilarityQueue", () => {
  it("keeps a new Model actionable while an old find resolves", async () => {
    function Subject() {
      const [id, setId] = useState(7);
      return (
        <>
          <button onClick={() => setId(8)}>Next Model</button>
          <SimilarityQueue modelId={id} />
        </>
      );
    }
    let finish!: (response: Response) => void;
    const app = renderApp(<Subject />, {
      routes: {
        "GET /api/v1/similarity/status": json(similarityStatus()),
        "GET /api/v1/similarity/candidates": json({ items: [], next_cursor: null }),
        "POST /api/v1/models/7/similar/query": () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Find similar" }));
    await userEvent.click(screen.getByRole("button", { name: "Next Model" }));
    expect(screen.getByRole("button", { name: "Find similar" })).toBeEnabled();
    await act(async () => {
      finish(
        json({
          items: [],
          next_cursor: null,
          run: aSimilarityRun({ id: 7, state: "completed", counters: { embedded: 99 } }),
        }),
      );
    });
    expect(screen.queryByText("99 visual vectors indexed")).not.toBeInTheDocument();
    expect(
      app.requestsWithMethod("GET").filter((request) => request.url.includes("/runs/7")),
    ).toEqual([]);
  });
  it("retries unavailable analysis status", async () => {
    const app = renderQueue(
      { routes: { "GET /api/v1/similarity/status": json({ detail: "unavailable" }, 503) } },
      7,
    );
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Find similar" })).toBeDisabled();
    app.route({ "GET /api/v1/similarity/status": json(similarityStatus()) });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Find similar" })).toBeEnabled());
  });
  it("retries an unavailable active run", async () => {
    const app = renderQueue(
      {
        routes: {
          "POST /api/v1/models/7/similar/query": json({
            items: [],
            next_cursor: null,
            run: aSimilarityRun({ id: 2, state: "running" }),
          }),
          "GET /api/v1/similarity/runs/2": json({ detail: "unavailable" }, 503),
        },
      },
      7,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Find similar" }));
    await screen.findByRole("alert");
    app.route({
      "GET /api/v1/similarity/runs/2": json(
        aSimilarityRun({ id: 2, state: "completed", counters: { embedded: 3 } }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("3 visual vectors indexed")).toBeVisible();
    expect(
      app.requestsWithMethod("GET").filter((request) => request.url.endsWith("/runs/2")),
    ).toHaveLength(2);
  });
  it("retains candidates after a continuation failure", async () => {
    let fail = true;
    renderQueue({
      routes: {
        "GET /api/v1/similarity/candidates": (url) => {
          if (!new URL(url, "http://test").searchParams.has("cursor"))
            return json({ items: [aSimilarityCandidate()], next_cursor: "next" });
          return fail
            ? json({ detail: "unavailable" }, 503)
            : json({
                items: [
                  aSimilarityCandidate({
                    id: 2,
                    model_b: {
                      id: 3,
                      name: "Recovered",
                      slug: "recovered",
                      thumbnail_file_id: null,
                    },
                  }),
                ],
                next_cursor: null,
              });
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Could not load similarity results")).toBeVisible();
    expect(screen.getByRole("link", { name: "Bracket" })).toBeVisible();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "Recovered" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: "Bracket" })).toHaveLength(2);
  });
  it("explains disabled analysis", async () => {
    renderQueue({
      routes: { "GET /api/v1/similarity/status": json(similarityStatus({ enabled: false })) },
    });
    expect(
      await screen.findByText(
        "New matches are paused. An administrator can enable analysis on the Similar models page.",
      ),
    ).toBeVisible();
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
      "/library/similar/1?returnTo=%2Flibrary%2Fsimilar",
    );
    expect(screen.getByRole("link", { name: "Bracket copy" })).toHaveAttribute("href", "/models/2");
  });
  it("switches review states directly from tabs", async () => {
    const app = renderQueue();
    await screen.findByText("No candidates to review");

    await userEvent.click(screen.getByRole("tab", { name: "Confirmed" }));

    expect(await screen.findByText("No confirmed matches")).toBeVisible();
    expect(screen.getByRole("tab", { name: "Confirmed" })).toHaveAttribute("aria-selected", "true");
    expect(app.requestsWithMethod("GET").at(-1)?.url).toContain("review_state=confirmed");
  });

  it("does not mistake an empty reviewed state for disabled analysis", async () => {
    renderQueue({
      at: "/library/similar?review=rejected",
      routes: { "GET /api/v1/similarity/status": json(similarityStatus({ enabled: false })) },
    });

    expect(await screen.findByText("No rejected matches")).toBeVisible();
    expect(screen.queryByText("Similarity analysis is off")).not.toBeInTheDocument();
    expect(
      screen.getByText("Pairs appear here after you choose this action in their comparison."),
    ).toBeVisible();
  });

  it("keeps active restrictions visible while filters are collapsed", async () => {
    renderQueue({ at: "/library/similar?format=stl&source=vault" });

    expect(await screen.findByText("No matches with these filters")).toBeVisible();
    expect(screen.getByRole("button", { name: "Filters2" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByText("STL · Vault")).toBeVisible();
  });

  it("clears restrictions without changing the selected review state", async () => {
    const app = renderQueue({ at: "/library/similar?review=later&format=stl&confidence=95" });
    await screen.findByText("No matches with these filters");

    await userEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[0]);

    expect(await screen.findByText("No matches saved for later")).toBeVisible();
    expect(screen.getByRole("tab", { name: "Later" })).toHaveAttribute("aria-selected", "true");
    expect(app.requestsWithMethod("GET").at(-1)?.url).toContain("review_state=later");
    expect(app.requestsWithMethod("GET").at(-1)?.url).not.toContain("minimum_confidence");
    expect(app.requestsWithMethod("GET").at(-1)?.url).not.toContain("file_type");
  });

  it("carries the review context into the comparison link", async () => {
    renderQueue({
      at: "/library/similar?review=later&format=stl",
      routes: {
        "GET /api/v1/similarity/candidates": json({
          items: [aSimilarityCandidate()],
          next_cursor: null,
        }),
      },
    });

    expect(await screen.findByRole("link", { name: "Compare" })).toHaveAttribute(
      "href",
      "/library/similar/1?returnTo=%2Flibrary%2Fsimilar%3Freview%3Dlater%26format%3Dstl",
    );
    expect(
      screen.getByText("Matching triangle geometry verified under a rigid transform."),
    ).toBeVisible();
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
    const user = userEvent.setup();
    renderQueue({
      locale: "es",
      routes: {
        "GET /api/v1/similarity/candidates": json({
          items: [aSimilarityCandidate({ freshness: "stale" })],
          next_cursor: null,
        }),
      },
    });
    await user.click(screen.getByRole("button", { name: /^Filtros/ }));
    const labels = await screen.findAllByText("Evidencia desactualizada");
    expect(labels.find((element) => element.tagName !== "OPTION")).toBeVisible();
  });
});

describe("Queue navigation", () => {
  it("retains rapid filter changes before candidate reads settle", async () => {
    const app = renderQueue({ at: "/library/similar?review=later" });
    await screen.findByText("No matches saved for later");
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));

    act(() => {
      fireEvent.change(screen.getByRole("combobox", { name: "All formats" }), {
        target: { value: "stl" },
      });
      fireEvent.change(screen.getByRole("combobox", { name: "All sources" }), {
        target: { value: "vault" },
      });
      const slider = screen.getByRole("slider", { name: "Minimum confidence" });
      for (const value of [100, 99, 98, 97, 96, 95, 94])
        fireEvent.change(slider, { target: { value } });
    });

    expect(screen.getByRole("slider", { name: "Minimum confidence" })).toHaveValue("94");
    expect(screen.getByRole("combobox", { name: "All formats" })).toHaveValue("stl");
    expect(screen.getByRole("combobox", { name: "All sources" })).toHaveValue("vault");
    await waitFor(() =>
      expect(screen.getByLabelText("Current queue location")).toHaveTextContent(
        "/library/similar?review=later&confidence=94&format=stl&source=vault",
      ),
    );
    await screen.findByText("No matches with these filters");
    const request = app.requestsWithMethod("GET").at(-1)?.url;
    expect(request).toContain("minimum_confidence=0.94");
    expect(request).toContain("file_type=stl");
    expect(request).toContain("source=vault");
    expect(request).toContain("review_state=later");
  });

  it("clears a pending confidence change without leaving the review state", async () => {
    const app = renderQueue({ at: "/library/similar?review=later&format=stl" });
    await screen.findByText("No matches with these filters");
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), {
      target: { value: "95" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[0]);

    expect(await screen.findByText("No matches saved for later")).toBeVisible();
    expect(screen.getByLabelText("Current queue location")).toHaveTextContent(
      "/library/similar?review=later",
    );
    expect(screen.getByRole("tab", { name: "Later" })).toHaveAttribute("aria-selected", "true");
    expect(app.requestsWithMethod("GET").at(-1)?.url).not.toContain("minimum_confidence");
  });

  it("restores filters from browser history", async () => {
    renderQueue({ at: "/library/similar?review=later&format=stl" });
    await screen.findByText("No matches with these filters");
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), {
      target: { value: "94" },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Current queue location")).toHaveTextContent("confidence=94"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Open another queue" }));
    expect(screen.getByRole("tab", { name: "Rejected" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("slider", { name: "Minimum confidence" })).toHaveValue("80");
    expect(screen.getByRole("combobox", { name: "All formats" })).toHaveValue("");

    await userEvent.click(screen.getByRole("button", { name: "Previous queue" }));
    expect(screen.getByRole("tab", { name: "Later" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("slider", { name: "Minimum confidence" })).toHaveValue("94");
    expect(screen.getByRole("combobox", { name: "All formats" })).toHaveValue("stl");
  });

  it("does not label a restored collection with the previously picked name", async () => {
    renderQueue({ at: "/library/similar" });
    await screen.findByText("No candidates to review");
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Collection" }), "pick");
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current queue location")).toHaveTextContent("collection=8"),
    );

    await userEvent.click(screen.getByRole("button", { name: "Open another queue" }));

    expect(screen.getByRole("combobox", { name: "Collection" })).toHaveValue("9");
    expect(screen.getByRole("option", { name: "Collection #9" })).toBeVisible();
    expect(screen.queryByRole("option", { name: "Parts" })).not.toBeInTheDocument();
  });

  it("returns a model comparison to its Similar tab with library context", async () => {
    renderQueue(
      {
        at: "/models/7?return_entry=abc&collection=parts&tab=similar",
        routes: {
          "GET /api/v1/similarity/candidates": json({
            items: [aSimilarityCandidate()],
            next_cursor: null,
          }),
        },
      },
      7,
    );

    const href = (await screen.findByRole("link", { name: "Compare" })).getAttribute("href")!;
    expect(new URL(href, "http://localhost").searchParams.get("returnTo")).toBe(
      "/models/7?return_entry=abc&collection=parts&tab=similar",
    );
  });

  it("finishes a model filter gesture that returns to its previous threshold", async () => {
    renderQueue({}, 7);
    await screen.findByText("No candidates to review");
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    const slider = screen.getByRole("slider", { name: "Minimum confidence" });
    fireEvent.change(slider, { target: { value: "95" } });
    await screen.findByText("No matches with these filters");
    fireEvent.change(slider, { target: { value: "75" } });
    fireEvent.change(slider, { target: { value: "95" } });

    expect(await screen.findByText("No matches with these filters")).toBeVisible();
    expect(slider).toHaveValue("95");
  });

  it.each([
    {
      label: "All evidence classes",
      key: "evidence_class",
      value: "remeshed",
      reset: "",
    },
    { label: "Current evidence", key: "freshness", value: "stale", reset: "current" },
    { label: "Collection", key: "collection_id", value: "8", reset: "" },
    { label: "All formats", key: "file_type", value: "3mf", reset: "" },
    { label: "All sources", key: "source", value: "external", reset: "" },
    { label: "All sources", key: "source", value: "vault", reset: "" },
  ])("sends the selected $key=$value filter", async ({ label, key, value, reset }) => {
    const user = userEvent.setup();
    const app = renderQueue({
      routes: {
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
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    if (key === "collection_id") {
      await user.selectOptions(screen.getByRole("combobox", { name: label }), "pick");
      await user.click(await screen.findByRole("option", { name: /Parts/ }));
    } else {
      await user.selectOptions(screen.getByRole("combobox", { name: label }), value);
    }
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
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
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
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), {
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
