/** Similarity commands cannot dispatch or publish after their initiating session retires. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useQuery } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMultipartModels } from "@/lib/queries";
import { aMultipartModel } from "@/test-support/factories";
import { aSimilarityCandidate } from "@/test-support/similarity";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import {
  similarityKeys,
  similarityStatusOptions,
  useSimilarityCommands,
} from "@/lib/queries/similarity";
import { similaritySettings, similarityStatus } from "@/test-support/similarity";
import { json, renderApp } from "@/test-support/render";

function SettingsCommand() {
  const status = useQuery(similarityStatusOptions());
  const { saveSettings } = useSimilarityCommands();
  return (
    <>
      <p>{status.data ? "Loaded" : "Loading"}</p>
      <button
        onClick={() =>
          saveSettings.mutate({ payload: { enabled: false }, session: getSessionVersion() })
        }
      >
        Save similarity
      </button>
    </>
  );
}
function renderCommand() {
  return renderApp(<SettingsCommand />, {
    routes: {
      "GET /api/v1/similarity/status": json(similarityStatus()),
      "PATCH /api/v1/similarity/settings": json(similaritySettings({ enabled: false })),
    },
  });
}
afterEach(() => vi.restoreAllMocks());
describe("similarity command lifetime", () => {
  it("never dispatches a retired gesture", async () => {
    const app = renderCommand();
    await screen.findByText("Loaded");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save similarity" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("PATCH")).toEqual([]);
    expect(app.client.getQueriesData({ queryKey: similarityKeys.all })).toEqual([]);
  });
  it("discards a retired acknowledgement after delayed publication", async () => {
    const app = renderCommand();
    await screen.findByText("Loaded");
    let resume!: () => void;
    const paused = new Promise<void>((resolve) => {
      resume = resolve;
    });
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused);
    await userEvent.click(screen.getByRole("button", { name: "Save similarity" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => {
      app.unmount();
      clearLogin();
      resume();
      await paused;
    });
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    expect(app.client.getQueriesData({ queryKey: similarityKeys.all })).toEqual([]);
  });
});

describe("similarity resolution publication", () => {
  it("refreshes the canonical Multipart catalog once after resolution", async () => {
    function Resolve() {
      const catalog = useMultipartModels();
      const { decide } = useSimilarityCommands();
      return (
        <>
          <p>{catalog.data?.[0]?.name}</p>
          <button
            onClick={() =>
              decide.mutate({
                id: 1,
                payload: {
                  action: "create_multipart",
                  request_id: "resolution-request",
                  version: 1,
                  name: "Confirmed assembly",
                },
                session: getSessionVersion(),
              })
            }
          >
            Resolve assembly
          </button>
        </>
      );
    }
    let resolved = false;
    const app = renderApp(<Resolve />, {
      routes: {
        "GET /api/v1/multipart-models": () =>
          json([aMultipartModel({ name: resolved ? "Confirmed assembly" : "Existing assembly" })]),
        "POST /api/v1/similarity/candidates/1/decision": () => {
          resolved = true;
          return json({
            decision_id: 1,
            target_id: 1,
            resolution_kind: "multipart",
            candidate: aSimilarityCandidate({
              resolution_kind: "multipart",
              review_state: "confirmed",
              version: 2,
            }),
          });
        },
      },
    });
    await screen.findByText("Existing assembly");
    await userEvent.click(screen.getByRole("button", { name: "Resolve assembly" }));
    expect(await screen.findByText("Confirmed assembly")).toBeVisible();
    expect(
      app.requestsWithMethod("GET").filter((request) => request.url === "/api/v1/multipart-models"),
    ).toHaveLength(2);
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
});
