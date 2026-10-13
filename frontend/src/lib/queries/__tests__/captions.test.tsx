/** Caption acknowledgements cannot recreate retired private snapshots. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useQuery, useInfiniteQuery } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { captionKeys, subjectCaptionOptions, useCaptionCommand } from "@/lib/queries/captions";
import { searchResultsOptions } from "@/lib/queries/search";
import { aSearchResult, searchResponse } from "@/test-support/search";
import { aCaption } from "@/test-support/captions";
import { json, renderApp } from "@/test-support/render";

function Editor() {
  const query = useQuery(subjectCaptionOptions(1, "model", 7));
  const command = useCaptionCommand();
  return (
    <>
      <p>{query.data?.text}</p>
      <button
        onClick={() =>
          command.mutate({
            userId: 1,
            type: "model",
            id: 7,
            body: { action: "edit", text: "My draft", version_token: "a".repeat(32) },
            session: getSessionVersion(),
          })
        }
      >
        Save caption
      </button>
    </>
  );
}
function renderCommand() {
  return renderApp(<Editor />, {
    routes: {
      "GET /api/v1/subjects/model/7/caption": json(aCaption()),
      "PATCH /api/v1/subjects/model/7/caption": json(
        aCaption({ text: "Acknowledged draft", version_token: "b".repeat(32) }),
      ),
    },
  });
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});
describe("caption command lifetime", () => {
  it("never dispatches a retired gesture", async () => {
    const app = renderCommand();
    await screen.findByText("A mounting bracket");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save caption" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
    expect(app.client.getQueriesData({ queryKey: ["subject-caption"] })).toHaveLength(0);
  });
  it("discards an acknowledgement after delayed publication retires", async () => {
    const app = renderCommand();
    await screen.findByText("A mounting bracket");
    let resume!: () => void;
    const paused = new Promise<void>((resolve) => {
      resume = resolve;
    });
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused);
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => {
      app.unmount();
      clearLogin();
      resume();
      await paused;
    });
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    expect(app.client.getQueriesData({ queryKey: ["subject-caption"] })).toHaveLength(0);
  });
  it("leaves a new same-user read active when a retired acknowledgement resumes", async () => {
    const app = renderCommand();
    await screen.findByText("A mounting bracket");
    const cache = app.client.getMutationCache();
    const previousSuccess = cache.config.onSuccess;
    const previousSettled = cache.config.onSettled;
    const entered = Promise.withResolvers<void>();
    const resume = Promise.withResolvers<void>();
    const settled = Promise.withResolvers<void>();
    cache.config.onSuccess = async () => {
      entered.resolve();
      await resume.promise;
    };
    cache.config.onSettled = () => {
      settled.resolve();
    };
    const currentRead = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    try {
      await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
      await entered.promise;
      app.unmount();
      clearLogin();
      renderApp(<Editor />, {
        routes: {
          "GET /api/v1/subjects/model/7/caption": (_url, init) => {
            signal = init?.signal;
            return currentRead.promise;
          },
        },
      });
      await waitFor(() => expect(signal).toBeDefined());
      await act(async () => {
        resume.resolve();
        await settled.promise;
      });
      expect(signal?.aborted).toBe(false);
      await act(async () => {
        currentRead.resolve(json(aCaption({ text: "Current session caption" })));
      });
      expect(await screen.findByText("Current session caption")).toBeVisible();
    } finally {
      cache.config.onSuccess = previousSuccess;
      cache.config.onSettled = previousSettled;
      resume.resolve();
      currentRead.resolve(json(aCaption()));
    }
  });
  it("refreshes the owning Search projection after a caption edit", async () => {
    function Results() {
      const query = useInfiniteQuery(
        searchResultsOptions(1, { kind: "text", query: { q: "bracket" } }, true),
      );
      return <p>{query.data?.pages[0].items[0]?.name}</p>;
    }
    let reads = 0;
    const app = renderApp(
      <>
        <Editor />
        <Results />
      </>,
      {
        routes: {
          "GET /api/v1/subjects/model/7/caption": json(aCaption()),
          "PATCH /api/v1/subjects/model/7/caption": json(aCaption({ text: "Acknowledged draft" })),
          "GET /api/v1/search?": () =>
            json(
              searchResponse({
                items: [
                  aSearchResult({ name: ++reads === 1 ? "Before caption" : "After caption" }),
                ],
              }),
            ),
        },
      },
    );
    await screen.findByText("Before caption");
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    expect(await screen.findByText("After caption")).toBeVisible();
    expect(screen.queryByText("Before caption")).toBeNull();
    expect(reads).toBe(2);
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it("publishes the authoritative acknowledgement without a second caption GET", async () => {
    const app = renderCommand();
    await screen.findByText("A mounting bracket");
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    expect(await screen.findByText("Acknowledged draft")).toBeVisible();
    expect(app.client.getQueryData(captionKeys.detail(1, "model", 7))).toEqual(
      aCaption({ text: "Acknowledged draft", version_token: "b".repeat(32) }),
    );
    expect(app.requests().filter(({ method }) => method === "GET")).toHaveLength(1);
  });
});

describe("caption generation observation", () => {
  it("stops polling after the generated caption settles", async () => {
    vi.useFakeTimers();
    let reads = 0;
    const app = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/subjects/model/7/caption": () =>
          json(
            ++reads === 1
              ? aCaption({ phase: "pending", text: "" })
              : aCaption({ phase: "ready", text: "Generated caption" }),
          ),
      },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(reads).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5001);
    });
    expect(screen.getByText("Generated caption")).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(reads).toBe(2);
    app.unmount();
  });
  it("stops polling after generation observation fails", async () => {
    vi.useFakeTimers();
    let reads = 0;
    const app = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/subjects/model/7/caption": () =>
          ++reads === 1
            ? json(aCaption({ phase: "running" }))
            : json({ detail: "Unavailable" }, 503),
      },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5001);
    });
    expect(reads).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(reads).toBe(2);
    app.unmount();
  });
});
