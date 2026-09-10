/** Evidence-only review preserves each Model. Stale evidence and missing previews remain explicit. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SimilarModelComparisonPage from "@/pages/similar-model-comparison";
import { aModel, aMultipartModel } from "@/test-support/factories";
import { aSimilarityCandidate } from "@/test-support/similarity";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { ReviewState, SimilarityCandidate } from "@/types/similarity";

function renderComparison(
  candidate: SimilarityCandidate = aSimilarityCandidate(),
  options: RenderAppOptions = {},
) {
  return renderApp(<SimilarModelComparisonPage />, {
    at: "/library/similar/1",
    routePath: "/library/similar/:id",
    ...options,
    routes: {
      "GET /api/v1/similarity/candidates/1": json(candidate),
      "GET /api/v1/models/1": json(aModel()),
      "GET /api/v1/models/2": json(aModel({ id: 2 })),
      "GET /api/v1/models/1/print-jobs": json([]),
      "GET /api/v1/models/2/print-jobs": json([]),
      "POST /api/v1/similarity/candidates/1/decision": json({
        decision_id: 7,
        target_id: null,
        resolution_kind: "evidence_only",
        candidate: {
          ...candidate,
          review_state: "confirmed",
          resolution_kind: "evidence_only",
          version: 2,
        },
      }),
      ...options.routes,
    },
  });
}
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SimilarModelComparisonPage", () => {
  it("confirms evidence with a durable request identity", async () => {
    const user = userEvent.setup();
    const rendered = renderComparison();
    await user.click(await screen.findByRole("button", { name: "Confirm evidence" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(/Each Model and its print context stays separate/),
    ).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Confirm evidence" }));
    await waitFor(() => expect(rendered.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(rendered.requestsWithMethod("POST")[0].body)).toEqual({
      action: "confirm_evidence",
      version: 1,
      request_id: expect.any(String),
    });
    expect(await screen.findByText("Resolution: evidence confirmed")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Family/ })).not.toBeInTheDocument();
  });
  it("hides confirmation when evidence is stale", async () => {
    renderComparison(
      aSimilarityCandidate({ freshness: "stale", allowed_actions: ["reject", "later", "reopen"] }),
    );
    expect(await screen.findByText("Stale evidence")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Confirm evidence" })).not.toBeInTheDocument();
  });
  it("keeps the metadata table usable without previews", async () => {
    renderComparison();
    expect(await screen.findByRole("table")).toBeVisible();
    expect(screen.getAllByText("3D preview unavailable").length).toBeGreaterThan(0);
    expect(screen.getByRole("rowheader", { name: "Original dimensions" })).toBeVisible();
    expect(screen.getByText("0.03 mm")).toBeVisible();
  });
  it("submits the verified plate quantity", async () => {
    const user = userEvent.setup();
    const rendered = renderComparison(
      aSimilarityCandidate({
        evidence_class: "plate_of",
        exact_equivalence: false,
        summary: { copies: 6, composition: [{ model_id: 1, quantity: 6 }] },
        allowed_actions: ["create_multipart", "confirm_evidence"],
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Create multipart model" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Quantity: 6")).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Create multipart model" }));
    await waitFor(() => expect(rendered.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(rendered.requestsWithMethod("POST")[0].body)).toMatchObject({
      action: "create_multipart",
      parts: [{ name: "Bracket", model_ids: [1], quantity: 6 }],
    });
  });
  it("keeps the same request identity after a failed confirmation", async () => {
    const user = userEvent.setup();
    const rendered = renderComparison(aSimilarityCandidate(), {
      routes: {
        "POST /api/v1/similarity/candidates/1/decision": json(
          { detail: "temporarily_unavailable" },
          503,
        ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Confirm evidence" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm evidence" }),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm evidence" }),
      ).toBeEnabled(),
    );
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm evidence" }),
    );
    await waitFor(() => expect(rendered.requestsWithMethod("POST")).toHaveLength(2));
    expect(rendered.requestsWithMethod("POST")[0].body).toBe(
      rendered.requestsWithMethod("POST")[1].body,
    );
  });
  it("renders review controls in Spanish", async () => {
    renderComparison(aSimilarityCandidate(), { locale: "es" });
    expect(await screen.findByRole("button", { name: "Confirmar evidencia" })).toBeVisible();
  });
});

describe("Multipart resolution", () => {
  it("adds verified quantities to an existing composition", async () => {
    const user = userEvent.setup();
    const app = renderComparison(
      aSimilarityCandidate({
        evidence_class: "plate_of",
        summary: { composition: [{ model_id: 1, quantity: 6 }] },
        allowed_actions: ["create_multipart"],
      }),
      {
        routes: {
          "GET /api/v1/multipart-models": json([
            aMultipartModel({ id: 12, name: "Workshop assembly", effective_role: "edit" }),
          ]),
        },
      },
    );
    await user.click(await screen.findByRole("button", { name: "Create multipart model" }));
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(await within(dialog).findByRole("combobox"), "12");
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Add reviewed parts" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    const payload = JSON.parse(app.requestsWithMethod("POST")[0].body);
    expect(payload).toMatchObject({
      action: "create_multipart",
      target_id: 12,
      parts: [{ name: "Bracket", model_ids: [1], quantity: 6 }],
    });
    expect(payload).not.toHaveProperty("name");
  });
});

describe("Multipart destination discovery", () => {
  const composition = () =>
    aSimilarityCandidate({
      evidence_class: "plate_of",
      summary: { composition: [{ model_id: 1, quantity: 6 }] },
      allowed_actions: ["create_multipart"],
    });
  it("finds destinations beyond the first page", async () => {
    const user = userEvent.setup();
    const firstPage = Array.from({ length: 30 }, (_, index) =>
      aMultipartModel({ id: index + 1, name: `Assembly ${index}`, effective_role: "edit" }),
    );
    renderComparison(composition(), {
      routes: {
        "GET /api/v1/multipart-models": (url) =>
          json(
            new URL(url, "http://localhost").searchParams.get("offset") === "30"
              ? [aMultipartModel({ id: 31, name: "Last assembly", effective_role: "edit" })]
              : firstPage,
          ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Create multipart model" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(await dialog.findByRole("button", { name: "Load more destinations" }));
    expect(await dialog.findByRole("option", { name: "Last assembly" })).toBeInTheDocument();
    expect(
      dialog.queryByRole("button", { name: "Load more destinations" }),
    ).not.toBeInTheDocument();
  });
  it("preserves the selected destination while searching", async () => {
    const user = userEvent.setup();
    const app = renderComparison(composition(), {
      routes: {
        "GET /api/v1/multipart-models": (url) =>
          json(
            new URL(url, "http://localhost").searchParams.get("q")
              ? []
              : [aMultipartModel({ id: 31, name: "Chosen assembly", effective_role: "edit" })],
          ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Create multipart model" }));
    const dialog = within(screen.getByRole("dialog"));
    await dialog.findByRole("option", { name: "Chosen assembly" });
    await user.selectOptions(dialog.getByRole("combobox"), "31");
    await user.click(dialog.getByRole("searchbox", { name: "Search existing multipart models" }));
    await user.paste("unrelated");
    await waitFor(() =>
      expect(
        app.requestsWithMethod("GET").some((request) => request.url.includes("q=unrelated")),
      ).toBe(true),
    );
    expect(dialog.getByRole("combobox")).toHaveValue("31");
    await user.click(dialog.getByRole("button", { name: "Add reviewed parts" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body).target_id).toBe(31);
  });
  it("keeps creation available when destination loading fails", async () => {
    const user = userEvent.setup();
    renderComparison(composition(), {
      routes: { "GET /api/v1/multipart-models": json({ detail: "unavailable" }, 503) },
    });
    await user.click(await screen.findByRole("button", { name: "Create multipart model" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(await dialog.findByRole("status")).toHaveTextContent("You can still create a new one");
    expect(dialog.getByRole("button", { name: "Create multipart model" })).toBeEnabled();
  });
});

describe("Comparison address validation", () => {
  it.each(["0", "-1", "invalid", "9007199254740992"])(
    "rejects invalid comparison address %s",
    async (id) => {
      const app = renderComparison(aSimilarityCandidate(), { at: `/library/similar/${id}` });
      expect(await screen.findByText("Could not load similarity results")).toBeVisible();
      expect(screen.getByRole("link", { name: "Back to similar models" })).toHaveAttribute(
        "href",
        "/library/similar",
      );
      expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
      expect(
        app
          .requestsWithMethod("GET")
          .filter((request) => request.url.includes("similarity/candidates")),
      ).toEqual([]);
    },
  );
});

describe("Review state controls", () => {
  it.each([
    { label: "Reject match", action: "reject", state: "open" },
    { label: "Review later", action: "later", state: "open" },
    { label: "Reopen review", action: "reopen", state: "rejected" },
  ] satisfies { label: string; action: string; state: ReviewState }[])(
    "submits $action",
    async ({ label, action, state }) => {
      const user = userEvent.setup();
      const app = renderComparison(aSimilarityCandidate({ review_state: state, version: 4 }));
      await user.click(await screen.findByRole("button", { name: label }));
      await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
      expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toEqual({
        action,
        version: 4,
        request_id: expect.any(String),
      });
    },
  );
  it.each(["Confirm evidence", "Create multipart model"])(
    "cancels %s without writing",
    async (label) => {
      const user = userEvent.setup();
      const app = renderComparison(
        aSimilarityCandidate({
          allowed_actions: ["confirm_evidence", "create_multipart"],
          summary: { composition: [{ model_id: 1, quantity: 2 }] },
        }),
      );
      await user.click(await screen.findByRole("button", { name: label }));
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(app.requestsWithMethod("POST")).toEqual([]);
    },
  );
  it("submits an edited Multipart name", async () => {
    const user = userEvent.setup();
    const app = renderComparison(
      aSimilarityCandidate({
        allowed_actions: ["create_multipart"],
        summary: { unmatched_components: 1, composition: [{ model_id: 2, quantity: 3 }] },
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Create multipart model" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.clear(dialog.getByRole("textbox"));
    expect(dialog.getByRole("button", { name: "Create multipart model" })).toBeDisabled();
    await user.click(dialog.getByRole("textbox"));
    await user.paste("  Workshop parts  ");
    await user.click(dialog.getByRole("button", { name: "Create multipart model" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      name: "Workshop parts",
      parts: [{ name: "Bracket copy", model_ids: [2], quantity: 3 }],
    });
  });
  it("retries a failed comparison request", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderComparison(aSimilarityCandidate(), {
      routes: {
        "GET /api/v1/similarity/candidates/1": () =>
          failed ? json({ detail: "unavailable" }, 503) : json(aSimilarityCandidate()),
      },
    });
    await screen.findByText("Could not load similarity results");
    failed = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Confirm evidence" })).toBeVisible();
  });
});

describe("Concurrent review recovery", () => {
  it("refreshes a conflicted review before retry", async () => {
    const user = userEvent.setup();
    let conflicted = false;
    const app = renderComparison(aSimilarityCandidate(), {
      routes: {
        "GET /api/v1/similarity/candidates/1": () =>
          json(aSimilarityCandidate({ version: conflicted ? 2 : 1 })),
        "POST /api/v1/similarity/candidates/1/decision": () => {
          conflicted = true;
          return json({ detail: "similarity_version_conflict" }, 409);
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Confirm evidence" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm evidence" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This evidence changed. Review the updated comparison before confirming again.",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Confirm evidence" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm evidence" }),
    );
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(2));
    const first = JSON.parse(app.requestsWithMethod("POST")[0].body);
    const second = JSON.parse(app.requestsWithMethod("POST")[1].body);
    expect(second.version).toBe(2);
    expect(second.request_id).not.toBe(first.request_id);
  });
});
