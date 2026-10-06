/** A retired inbox acknowledgement cannot cancel or replace reads from the next private incarnation. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { json, renderApp } from "@/test-support/render";
import { FROZEN_NOW } from "@/test-support/factories";
import { useInboxItem, useInboxCommands, inboxApi } from "@/lib/queries/inbox";
import type { InboxItem } from "@/types";
const item: InboxItem = {
  id: 1,
  owner_user_id: 1,
  source_kind: "url",
  source_url: "https://example.test/model",
  display_title: "Earlier inbox",
  source_hostname: "example.test",
  state: "review",
  manifest: { kind: "direct" },
  target_collection_id: null,
  requested_tags: [],
  job_id: null,
  resulting_model_id: null,
  results: [],
  error_code: null,
  retryable: false,
  attempt_count: 0,
  created_at: FROZEN_NOW,
  updated_at: FROZEN_NOW,
  completed_at: null,
  completion: null,
};
const KINDS = ["dismiss", "batch", "batch-dismiss", "retry", "update", "import"] as const;
type Kind = (typeof KINDS)[number];
function Editor({ kind }: { kind: Kind }) {
  const read = useInboxItem(1, inboxApi, false);
  const commands = useInboxCommands();
  function save() {
    const session = getSessionVersion();
    switch (kind) {
      case "dismiss":
        commands.dismiss.mutate({ id: 1, session });
        break;
      case "batch":
        commands.batch.mutate({ payload: { item_ids: [1], action: "retry" }, session });
        break;
      case "batch-dismiss":
        commands.batch.mutate({ payload: { item_ids: [1], action: "dismiss" }, session });
        break;
      case "retry":
        commands.retry.mutate({ id: 1, session });
        break;
      case "update":
        commands.update.mutate({ id: 1, payload: { title: "Saved" }, session });
        break;
      case "import":
        commands.import.mutate({ id: 1, selectedIds: [], session });
        break;
    }
  }
  return (
    <>
      <p>{read.data?.display_title}</p>
      <button onClick={save}>Save</button>
    </>
  );
}
function renderCommand(kind: Kind) {
  return renderApp(<Editor kind={kind} />, {
    routes: {
      "GET /api/v1/inbox/1": json(item),
      "DELETE /api/v1/inbox/1": json(null, 204),
      "POST /api/v1/inbox/batch": json([{ ...item, display_title: "Saved" }]),
      "POST /api/v1/inbox/1": json({ ...item, display_title: "Saved" }),
      "PATCH /api/v1/inbox/1": json({ ...item, display_title: "Saved" }),
    },
  });
}

afterEach(() => vi.restoreAllMocks());
describe("retired inbox acknowledgements", () => {
  it.each(KINDS.map((kind) => ({ kind })))(
    "leaves a new inbox read active after retired $kind acknowledgement",
    async ({ kind }) => {
      const app = renderCommand(kind);
      await screen.findByText("Earlier inbox");
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
      cache.config.onSettled = () => settled.resolve();
      const currentRead = Promise.withResolvers<Response>();
      let signal: AbortSignal | null | undefined;
      try {
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await entered.promise;
        app.unmount();
        clearLogin();
        renderApp(<Editor kind={kind} />, {
          routes: {
            "GET /api/v1/inbox/1": (_url, init) => {
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
        await act(async () =>
          currentRead.resolve(json({ ...item, display_title: "Current inbox" })),
        );
        expect(await screen.findByText("Current inbox")).toBeVisible();
      } finally {
        cache.config.onSuccess = previousSuccess;
        cache.config.onSettled = previousSettled;
        resume.resolve();
        currentRead.resolve(json({ ...item, display_title: "Current inbox" }));
      }
    },
  );
  it("discards an inbox acknowledgement after cancellation retires", async () => {
    const app = renderCommand("update");
    await screen.findByText("Earlier inbox");
    const cache = app.client.getMutationCache();
    const previousSettled = cache.config.onSettled;
    const settled = Promise.withResolvers<void>();
    cache.config.onSettled = () => settled.resolve();
    const paused = Promise.withResolvers<void>();
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi.spyOn(app.client, "cancelQueries");
    spy.mockImplementationOnce(cancel);
    spy.mockImplementationOnce(() => paused.promise);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
      await act(async () => {
        app.unmount();
        clearLogin();
        paused.resolve();
        await settled.promise;
      });
      expect(app.client.getQueriesData({ queryKey: ["inbox"] })).toEqual([]);
    } finally {
      cache.config.onSettled = previousSettled;
      paused.resolve();
    }
  });
  it("rejects an empty inbox batch after cancellation retires", async () => {
    const app = renderCommand("batch");
    app.route({ "POST /api/v1/inbox/batch": json([]) });
    await screen.findByText("Earlier inbox");
    const cache = app.client.getMutationCache();
    const previousSettled = cache.config.onSettled;
    const settled = Promise.withResolvers<unknown>();
    cache.config.onSettled = (_data, error) => settled.resolve(error);
    const paused = Promise.withResolvers<void>();
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused.promise);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
      await act(async () => {
        app.unmount();
        clearLogin();
        paused.resolve();
      });
      expect(await settled.promise).toMatchObject({ name: "AbortError" });
    } finally {
      cache.config.onSettled = previousSettled;
      paused.resolve();
    }
  });
});
