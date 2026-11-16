/** Reports the chosen Artifact's evidence without certifying print safety. */
import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { renderApp, json } from "@/test-support/render";
import { aDerivative } from "@/test-support/factories";
import { GeometryReport } from "../geometry-report";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";

describe("GeometryReport", () => {
  it("refreshes file analysis after its background result changes", async () => {
    const socket: EventSocket = {
      onopen: null,
      onclose: null,
      onmessage: null,
      send: () => {},
      close: () => {},
    };
    setEventSocketFactory(async () => socket);
    const view = renderApp(<GeometryReport modelId={1} fileId={100} volume={null} />, {
      routes: {
        "GET /api/v1/files/100/derivatives": json([
          aDerivative({ kind: "metadata", state: "pending" }),
        ]),
      },
    });
    await waitFor(() =>
      expect(view.requests().some((r) => r.url.endsWith("/derivatives"))).toBe(true),
    );
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    view.route({
      "GET /api/v1/files/100/derivatives": json([
        aDerivative({ kind: "metadata", state: "ready" }),
      ]),
    });
    act(() =>
      socket.onmessage?.({
        data: JSON.stringify({
          type: "derivative",
          model_id: 1,
          file_id: 100,
          kind: "metadata",
          state: "ready",
        }),
      }),
    );
    expect(await screen.findByText("Passed")).toBeVisible();
    expect(screen.getAllByText("Not checked")).toHaveLength(3);
  });
  it("keeps unsupported geometry visibly unchecked", async () => {
    renderApp(<GeometryReport modelId={1} fileId={100} volume={null} />, {
      routes: {
        "GET /api/v1/files/100/derivatives": json([
          aDerivative({
            kind: "metadata",
            state: "failed",
            failure_reason: "unsupported_capability",
          }),
        ]),
      },
    });
    expect(
      await screen.findByText(
        "Analysis was not completed because this format, feature or resource requirement is not supported.",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "Geometry checks" })).getAllByText("Not checked"),
    ).toHaveLength(4);
    expect(screen.getByText(/Advisory geometry evidence only/)).toBeVisible();
  });
  it("shows file analysis failure without inventing topology results", async () => {
    renderApp(<GeometryReport modelId={1} fileId={100} volume={null} />, {
      routes: {
        "GET /api/v1/files/100/derivatives": json([
          aDerivative({ kind: "metadata", state: "failed", failure_reason: "invalid_source" }),
        ]),
      },
    });
    expect(await screen.findByText("Failed")).toBeVisible();
    expect(screen.getAllByText("Not checked")).toHaveLength(3);
  });
  it("explains a failed status request", async () => {
    renderApp(<GeometryReport modelId={1} fileId={100} volume={null} />, {
      routes: { "GET /api/v1/files/100/derivatives": () => new Response("", { status: 503 }) },
    });
    expect(await screen.findByText("Analysis status could not be loaded.")).toBeVisible();
    expect(screen.queryByText("Passed")).not.toBeInTheDocument();
  });
});
