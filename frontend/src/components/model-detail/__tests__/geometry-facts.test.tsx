/** Reports the chosen Artifact's evidence without certifying print safety. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { renderApp, json } from "@/test-support/render";
import { aDerivative, aMetadata, aRevision } from "@/test-support/factories";
import { GeometryFacts } from "../geometry-facts";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";

const file = aRevision({ id: 100, file_type: "stl" });

function show(derivatives: () => Response, source = file) {
  return renderApp(
    <dl>
      <GeometryFacts modelId={1} sourceFile={source} />
    </dl>,
    { routes: { "GET /api/v1/files/100/derivatives": derivatives } },
  );
}

describe("GeometryFacts", () => {
  it("refreshes file analysis after its background result changes", async () => {
    const socket: EventSocket = {
      onopen: null,
      onclose: null,
      onmessage: null,
      send: () => {},
      close: () => {},
    };
    setEventSocketFactory(async () => socket);
    const view = renderApp(
      <dl>
        <GeometryFacts modelId={1} sourceFile={file} />
      </dl>,
      {
        routes: {
          "GET /api/v1/files/100/derivatives": json([
            aDerivative({ kind: "metadata", state: "pending" }),
          ]),
        },
      },
    );
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
    expect(screen.getAllByText(/Not checked/)).toHaveLength(3);
  });

  it("treats unsupported geometry as an unchecked advisory", async () => {
    show(() =>
      json([
        aDerivative({
          kind: "metadata",
          state: "failed",
          failure_reason: "unsupported_capability",
        }),
      ]),
    );
    expect(
      await screen.findByText(
        "Analysis was not completed because this format, feature or resource requirement is not supported.",
      ),
    ).toBeVisible();
    const row = screen.getByText("Geometry checks").closest("div");
    if (!row) throw new Error("A fact label must belong to its row");
    expect(within(row).getAllByText(/Not checked/)).toHaveLength(4);
    expect(screen.getByText(/Advisory geometry evidence only/)).toBeVisible();
  });

  it("shows file analysis failure without inventing topology results", async () => {
    show(() =>
      json([aDerivative({ kind: "metadata", state: "failed", failure_reason: "invalid_source" })]),
    );
    expect(await screen.findByText(/Failed/)).toBeVisible();
    expect(screen.getAllByText(/Not checked/)).toHaveLength(3);
  });

  it("explains a failed status request", async () => {
    show(() => new Response("", { status: 503 }));
    expect(await screen.findByText("Analysis status could not be loaded.")).toBeVisible();
    expect(screen.queryByText("Passed")).not.toBeInTheDocument();
  });

  it("lists measured geometry facts", async () => {
    show(
      () => json([]),
      aRevision({
        id: 100,
        file_type: "stl",
        metadata: aMetadata({
          bbox_x_mm: 10,
          bbox_y_mm: 20,
          bbox_z_mm: 30,
          volume_mm3: 2000,
          triangle_count: 12,
        }),
      }),
    );
    expect(await screen.findByText("10 × 20 × 30 mm")).toBeVisible();
    expect(screen.getByText("2 cm³")).toBeVisible();
    expect(screen.getByText("12")).toBeVisible();
  });

  it("explains unavailable mesh measurements once", async () => {
    show(() => json([]));
    expect(await screen.findByText("Measurement unavailable")).toBeVisible();
  });

  it("does not discard recorded zero geometry", async () => {
    show(
      () => json([]),
      aRevision({
        id: 100,
        file_type: "stl",
        metadata: aMetadata({
          bbox_x_mm: 0,
          bbox_y_mm: 12,
          bbox_z_mm: 30,
          volume_mm3: 0,
          triangle_count: 0,
        }),
      }),
    );
    expect(await screen.findByText("0 × 12 × 30 mm")).toBeVisible();
    expect(screen.getByText("0 cm³")).toBeVisible();
    expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  });
});
