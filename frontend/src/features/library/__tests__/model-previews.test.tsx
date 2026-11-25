/** Recreating a Model's views queues the work, then refreshes the library and the activity count. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useRegenerateModelPreviews } from "../model-previews";
import { queryKeys } from "@/lib/query-client";
import { json, renderApp } from "@/test-support/render";

function Probe() {
  const regenerate = useRegenerateModelPreviews();
  return <button onClick={() => void regenerate(7)}>Recreate</button>;
}

describe("useRegenerateModelPreviews", () => {
  it("asks the Model to recreate its views and invalidates what shows them", async () => {
    const user = userEvent.setup();
    const view = renderApp(<Probe />, {
      seed: [
        [queryKeys.models, { stale: false }],
        [queryKeys.previewActivity, { stale: false }],
      ],
      routes: { "POST /api/v1/models/7/previews/regenerate": () => json({ queued: 1 }, 202) },
    });

    await user.click(screen.getByRole("button", { name: "Recreate" }));

    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(view.requestsWithMethod("POST")[0]?.url).toContain(
      "/api/v1/models/7/previews/regenerate",
    );
    await waitFor(() => {
      expect(view.client.getQueryState(queryKeys.models)?.isInvalidated).toBe(true);
      expect(view.client.getQueryState(queryKeys.previewActivity)?.isInvalidated).toBe(true);
    });
  });
});
