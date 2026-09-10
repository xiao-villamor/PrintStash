/** Semantic neighbors are opt-in, authorized links without equivalence actions. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SimilaritySearch } from "@/components/similarity-search";
import { similarityStatus } from "@/test-support/similarity";
import { json, renderApp } from "@/test-support/render";
import type { SemanticNeighbors } from "@/types/similarity";

const neighbors: SemanticNeighbors = {
  items: [
    {
      model: { id: 2, name: "Cup", slug: "cup", thumbnail_file_id: null },
      score: 0.873,
      evidence_kind: "semantic",
    },
  ],
  evidence_kind: "semantic",
  space_id: 1,
  index_state: "ready",
  scanned: 3,
  truncated: false,
};
function renderSearch({
  text = true,
  available = true,
  modelId,
  response = neighbors,
  code = 200,
}: {
  text?: boolean;
  available?: boolean;
  modelId?: number;
  response?: SemanticNeighbors;
  code?: number;
} = {}) {
  return renderApp(<SimilaritySearch modelId={modelId} />, {
    routes: {
      "GET /api/v1/similarity/status": json(
        similarityStatus({
          capabilities: {
            family_resolution: false,
            multipart_resolution: true,
            step: true,
            local_embeddings: available,
            ["text_to_shape"]: text,
          },
        }),
      ),
      "POST /api/v1/similarity/search": json(response, code),
    },
  });
}
describe("SimilaritySearch", () => {
  it("submits the entered description", async () => {
    const app = renderSearch();
    const user = userEvent.setup();
    await user.type(
      await screen.findByRole("textbox", { name: "Describe the Model you are looking for" }),
      "a cup",
    );
    await user.click(screen.getByRole("button", { name: "Search by description" }));
    await waitFor(() =>
      expect(app.requestsWithMethod("POST")).toMatchObject([
        { body: JSON.stringify({ text: "a cup" }) },
      ]),
    );
  });
  it("labels semantic neighbors as approximate results", async () => {
    renderSearch();
    const user = userEvent.setup();
    await user.type(
      await screen.findByRole("textbox", { name: "Describe the Model you are looking for" }),
      "a cup",
    );
    await user.click(screen.getByRole("button", { name: "Search by description" }));
    expect(await screen.findByRole("link", { name: "Cup" })).toHaveAttribute("href", "/models/2");
    expect(screen.getByLabelText("Semantic similarity score")).toHaveTextContent("0.873");
    expect(screen.queryByRole("button", { name: /Confirm/ })).not.toBeInTheDocument();
  });
  it("supports Model neighbors for an image-only provider", async () => {
    const app = renderSearch({ text: false, modelId: 7 });
    await userEvent.click(await screen.findByRole("button", { name: "Find visual neighbors" }));
    expect(await screen.findByRole("link", { name: "Cup" })).toBeVisible();
    expect(app.requestsWithMethod("POST")).toMatchObject([
      { body: JSON.stringify({ model_id: 7 }) },
    ]);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
  it.each([{ text: false }, { available: false }])(
    "hides description search when unsupported: %j",
    async (options) => {
      const app = renderSearch(options);
      await waitFor(() =>
        expect(
          app.requests().filter((request) => request.url === "/api/v1/similarity/status"),
        ).toHaveLength(1),
      );
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    },
  );
  it("explains missing Model vectors", async () => {
    renderSearch({
      modelId: 7,
      response: { ...neighbors, items: [], index_state: "missing_model_vectors" },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Find visual neighbors" }));
    expect(await screen.findByText(/This Model has no current vectors/)).toBeVisible();
  });
  it("reports truncated results", async () => {
    renderSearch({ modelId: 7, response: { ...neighbors, truncated: true } });
    await userEvent.click(await screen.findByRole("button", { name: "Find visual neighbors" }));
    expect(await screen.findByText(/The search reached its scan limit/)).toBeVisible();
  });
  it("keeps the query available after a native failure", async () => {
    renderSearch({ modelId: 7, code: 409 });
    await userEvent.click(await screen.findByRole("button", { name: "Find visual neighbors" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Search is unavailable");
    expect(screen.getByRole("button", { name: "Find visual neighbors" })).toBeEnabled();
  });
});
