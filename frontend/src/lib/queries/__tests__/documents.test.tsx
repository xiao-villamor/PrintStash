/** A retired documents acknowledgement cannot cancel or replace reads from the next private incarnation. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { json, renderApp } from "@/test-support/render";
import { anEditingBase, FROZEN_NOW } from "@/test-support/factories";
import { documentKeys, useDocument, useDocumentMutations } from "@/lib/queries/documents";
import type { DocumentRead } from "@/types";
const document: DocumentRead = {
  id: 3,
  edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  edit_version: 1,
  name: "Earlier document",
  kind: "markdown",
  collection: "parts",
  collection_id: 1,
  multipart_model_id: null,
  filename: null,
  effective_role: "edit",
  updated_at: FROZEN_NOW,
  body: "Notes",
};
const KINDS = ["create", "upload", "update", "remove"] as const;
type Kind = (typeof KINDS)[number];
function Editor({ kind }: { kind: Kind }) {
  const read = useDocument(3);
  const commands = useDocumentMutations();
  function save() {
    const session = getSessionVersion();
    switch (kind) {
      case "create":
        commands.create.mutate({
          payload: { name: "Saved", collection_id: 1, body: "Notes" },
          session,
        });
        break;
      case "upload":
        commands.upload.mutate({ file: new File(["Notes"], "notes.md"), collectionId: 1, session });
        break;
      case "update":
        commands.update.mutate({
          id: 3,
          payload: { name: "Saved" },
          editVersion: anEditingBase(),
          session,
        });
        break;
      case "remove":
        commands.remove.mutate({ id: 3, session });
        break;
    }
  }
  return (
    <>
      <p>{read.data?.name}</p>
      <p>{commands.update.status}</p>
      <button onClick={save}>Save</button>
    </>
  );
}
function renderCommand(kind: Kind) {
  return renderApp(<Editor kind={kind} />, {
    routes: {
      "GET /api/v1/documents/3": json(document),
      "POST /api/v1/documents": json({ ...document, name: "Saved" }),
      "PUT /api/v1/documents/3": json({ ...document, name: "Saved", edit_version: 2 }),
      "DELETE /api/v1/documents/3": json(null, 204),
    },
  });
}

afterEach(() => vi.restoreAllMocks());
describe("retired document acknowledgements", () => {
  it("preserves a restored document after an earlier history acknowledges", async () => {
    const reply = Promise.withResolvers<Response>();
    const app = renderApp(<Editor kind="update" />, {
      routes: {
        "GET /api/v1/documents/3": json(document),
        "PUT /api/v1/documents/3": () => reply.promise,
      },
    });
    await screen.findByText("Earlier document");
    act(() => app.client.setQueryData(documentKeys.list(null), [document]));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(app.requests().some((request) => request.method === "PUT")).toBe(true),
    );
    const restored = {
      ...document,
      edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      name: "Restored document",
    };
    act(() => {
      app.client.setQueryData(documentKeys.detail(3), restored);
      app.client.setQueryData(documentKeys.list(null), [restored]);
    });
    await screen.findByText("Restored document");
    await act(async () => reply.resolve(json({ ...document, edit_version: 2, name: "Saved" })));
    await waitFor(() => expect(app.client.isMutating()).toBe(0));
    expect(screen.getByText("Restored document")).toBeVisible();
    expect(app.client.getQueryData(documentKeys.list(null))).toEqual([restored]);
    expect(screen.getByText("error")).toBeVisible();
  });

  it.each(KINDS.map((kind) => ({ kind })))(
    "leaves a new document read active after retired $kind acknowledgement",
    async ({ kind }) => {
      const app = renderCommand(kind);
      await screen.findByText("Earlier document");
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
            "GET /api/v1/documents/3": (_url, init) => {
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
        await act(async () => currentRead.resolve(json({ ...document, name: "Current document" })));
        expect(await screen.findByText("Current document")).toBeVisible();
      } finally {
        cache.config.onSuccess = previousSuccess;
        cache.config.onSettled = previousSettled;
        resume.resolve();
        currentRead.resolve(json({ ...document, name: "Current document" }));
      }
    },
  );
  it("discards a document acknowledgement after cancellation retires", async () => {
    const app = renderCommand("update");
    await screen.findByText("Earlier document");
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
      expect(app.client.getQueriesData({ queryKey: ["documents"] })).toEqual([]);
    } finally {
      cache.config.onSettled = previousSettled;
      paused.resolve();
    }
  });
});
