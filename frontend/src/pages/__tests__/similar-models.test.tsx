/** Similar Models opens on matches; administrative workspaces retain drafts across tabs. */
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SimilarModelsPage from "@/pages/similar-models";
import { aModel } from "@/test-support/factories";
import {
  aSimilarityCandidate,
  similaritySettings,
  similarityStatus,
} from "@/test-support/similarity";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import { resetTasksForNewSetup } from "@/lib/task-center";

function renderSimilar(options: RenderAppOptions = {}) {
  return renderApp(<SimilarModelsPage />, {
    at: "/library/similar",
    ...options,
    routes: {
      "GET /api/v1/similarity/status": json(similarityStatus()),
      "GET /api/v1/similarity/runs": json({ items: [], next_cursor: null }),
      "GET /api/v1/similarity/candidates": json({
        items: [aSimilarityCandidate()],
        next_cursor: null,
      }),
      "GET /api/v1/libraries": json([]),
      "POST /api/v1/similarity/selection-preview": json({ total: 1, by_class: {} }),
      "GET /api/v1/models": json([aModel({ id: 7, name: "Gear" })]),
      ...options.routes,
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetTasksForNewSetup();
});

describe("SimilarModelsPage", () => {
  it("opens on matches with administrative forms out of the way", async () => {
    renderSimilar();
    expect(screen.getByRole("tab", { name: "Matches" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("link", { name: "Compare" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeVisible();
    expect(
      screen.queryByRole("switch", { name: "Enable similarity analysis" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Analysis history" })).not.toBeInTheDocument();
  });

  it("opens settings from the disabled analysis explanation", async () => {
    const user = userEvent.setup();
    renderSimilar({
      routes: {
        "GET /api/v1/similarity/status": json(
          similarityStatus({ enabled: false, settings: similaritySettings({ enabled: false }) }),
        ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Open analysis settings" }));
    expect(screen.getByRole("tab", { name: "Analysis settings" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("switch", { name: "Enable similarity analysis" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Start analysis" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Compare" })).not.toBeInTheDocument();
  });

  it("retains an unsaved settings draft across workspace tabs", async () => {
    const user = userEvent.setup();
    const app = renderSimilar();
    await user.click(screen.getByRole("tab", { name: "Analysis settings" }));
    const threshold = await screen.findByRole("slider");
    fireEvent.change(threshold, { target: { value: "92" } });
    await user.click(screen.getByRole("switch", { name: "Enable similarity analysis" }));
    await user.click(screen.getByRole("tab", { name: "Analysis history" }));
    expect(await screen.findByText("No analysis runs yet")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Matches" }));
    await user.click(screen.getByRole("tab", { name: "Analysis settings" }));
    expect(screen.getByRole("slider")).toHaveValue("92");
    expect(screen.getByRole("switch", { name: "Enable similarity analysis" })).not.toBeChecked();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
  });

  it("retains selected scan Models across workspace tabs", async () => {
    const user = userEvent.setup();
    renderSimilar();
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Analysis scope" }),
      "models",
    );
    await user.click(await screen.findByRole("checkbox", { name: "Gear" }));
    await user.click(screen.getByRole("tab", { name: "Analysis settings" }));
    await user.click(screen.getByRole("tab", { name: "Matches" }));
    expect(screen.getByRole("combobox", { name: "Analysis scope" })).toHaveValue("models");
    expect(screen.getByRole("checkbox", { name: "Gear" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
  });

  it("keeps administrative workspaces hidden from ordinary members", async () => {
    const app = renderSimilar({ auth: memberSession() });
    expect(await screen.findByRole("link", { name: "Compare" })).toBeVisible();
    expect(screen.queryByRole("tab", { name: "Analysis settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Analysis history" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start analysis" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("switch", { name: "Enable similarity analysis" }),
    ).not.toBeInTheDocument();
    expect(
      app.requestsWithMethod("GET").some((request) => request.url === "/api/v1/similarity/runs"),
    ).toBe(false);
  });
});
