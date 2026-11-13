/** Visible cards share the detail cache without eagerly reading the whole library or retaining retired sessions. */
import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCardDetail, cardDimensions } from "../card-detail";
import { aModel, aMetadata, aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import { queryKeys } from "@/lib/query-client";
import { retirePrivateSessionScope } from "@/lib/auth-store";

function Card({ id = 1, enabled = true }: { id?: number; enabled?: boolean }) {
  const { ref, data } = useCardDetail(id, enabled);
  return (
    <div ref={ref} aria-label={`Card ${id}`}>
      {data?.name ?? "Pending"}
    </div>
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("useCardDetail", () => {
  it("does not read metadata before viewport admission", async () => {
    const observed = new Set<Element>();
    let enter: (node: Element) => void = () => {};
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: (entries: { target: Element; isIntersecting: boolean }[]) => void) {
          enter = (node) => callback([{ target: node, isIntersecting: true }]);
        }
        observe(node: Element) {
          observed.add(node);
        }
        unobserve(node: Element) {
          observed.delete(node);
        }
        disconnect() {
          observed.clear();
        }
      },
    );
    const app = renderApp(
      <>
        <Card />
        <Card id={2} />
      </>,
      { routes: { "GET /api/v1/models/1": json(aModel({ name: "Visible model" })) } },
    );
    expect(app.requests()).toEqual([]);
    act(() => enter(screen.getByLabelText("Card 1")));
    await screen.findByText("Visible model");
    expect(app.requests().map((request) => request.url)).toEqual([
      expect.stringContaining("/models/1"),
    ]);
    expect(screen.getByLabelText("Card 2").textContent).toBe("Pending");
  });
  it("reuses the inspector's cached model", () => {
    const app = renderApp(<Card />, {
      seed: [[queryKeys.model(1), aModel({ name: "Cached model" })]],
    });
    expect(screen.getByText("Cached model")).toBeTruthy();
    expect(app.requests()).toEqual([]);
  });
  it("deduplicates concurrent readers of the same model", async () => {
    const app = renderApp(
      <>
        <Card />
        <Card />
      </>,
      { routes: { "GET /api/v1/models/1": json(aModel({ name: "Shared model" })) } },
    );
    await waitFor(() => expect(screen.getAllByText("Shared model")).toHaveLength(2));
    expect(app.requests()).toHaveLength(1);
  });
  it("keeps disabled cards out of the metadata read", () => {
    const app = renderApp(<Card enabled={false} />);
    expect(screen.getByText("Pending")).toBeTruthy();
    expect(app.requests()).toEqual([]);
  });
  it("hides a retired session's cached metadata", async () => {
    renderApp(<Card />, { seed: [[queryKeys.model(1), aModel({ name: "Private model" })]] });
    expect(screen.getByText("Private model")).toBeTruthy();
    act(() => retirePrivateSessionScope());
    await waitFor(() => expect(screen.queryByText("Private model")).toBeNull());
    expect(screen.getByText("Pending")).toBeTruthy();
  });
});

describe("cardDimensions", () => {
  it("shows all three recorded source dimensions", () => {
    expect(
      cardDimensions(
        aModel({
          files: [
            aRevision({
              file_type: "stl",
              metadata: aMetadata({ bbox_x_mm: 210, bbox_y_mm: 110, bbox_z_mm: 40 }),
            }),
          ],
        }),
      ),
    ).toBe("210 × 110 × 40 mm");
  });
  it("omits incomplete source geometry", () => {
    expect(
      cardDimensions(
        aModel({
          files: [
            aRevision({
              file_type: "stl",
              metadata: aMetadata({ bbox_x_mm: 210, bbox_y_mm: 110, bbox_z_mm: null }),
            }),
          ],
        }),
      ),
    ).toBeNull();
  });
  it("does not substitute toolpath bounds for source geometry", () => {
    expect(
      cardDimensions(
        aModel({
          files: [
            aRevision({ metadata: aMetadata({ bbox_x_mm: 210, bbox_y_mm: 110, bbox_z_mm: 40 }) }),
          ],
        }),
      ),
    ).toBeNull();
  });
});
