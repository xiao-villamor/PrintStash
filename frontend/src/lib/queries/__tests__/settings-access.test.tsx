/** Direct permission acknowledgements affect only their captured private resource. */
import { useQuery } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  accessKeys,
  collectionAccessOptions,
  printerAccessOptions,
  useCollectionAccessCommand,
  usePrinterAccessCommand,
} from "@/lib/queries/settings-access";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { aCollectionPermission, aPrinterPermission } from "@/test-support/permissions";
import { json, renderApp } from "@/test-support/render";

function CollectionEditor() {
  const query = useQuery(collectionAccessOptions(1, 5));
  const command = useCollectionAccessCommand();
  return (
    <>
      {query.data?.map((row) => (
        <p key={row.user_id}>
          {row.username}:{row.role}
        </p>
      ))}
      <button
        onClick={() =>
          command.mutate({
            kind: "grant",
            collectionId: 5,
            targetUserId: 2,
            actorId: 1,
            session: getSessionVersion(),
            role: "view",
          })
        }
      >
        Grant
      </button>
      <button
        onClick={() =>
          command.mutate({
            kind: "revoke",
            collectionId: 5,
            targetUserId: 2,
            actorId: 1,
            session: getSessionVersion(),
          })
        }
      >
        Revoke
      </button>
    </>
  );
}
function PrinterEditor() {
  const query = useQuery(printerAccessOptions(1, 4));
  const command = usePrinterAccessCommand();
  return (
    <>
      {query.data?.map((row) => (
        <p key={row.user_id}>
          {row.username}:{row.role}
        </p>
      ))}
      <button
        onClick={() =>
          command.mutate({
            kind: "grant",
            printerId: 4,
            targetUserId: 2,
            actorId: 1,
            session: getSessionVersion(),
            role: "view",
          })
        }
      >
        Grant
      </button>
      <button
        onClick={() =>
          command.mutate({
            kind: "revoke",
            printerId: 4,
            targetUserId: 2,
            actorId: 1,
            session: getSessionVersion(),
          })
        }
      >
        Revoke
      </button>
    </>
  );
}
const resources = [
  {
    label: "collection",
    Editor: CollectionEditor,
    path: "/api/v1/collections/5/permissions",
    key: accessKeys.collection(1, 5),
    base: aCollectionPermission(),
    current: aCollectionPermission({ username: "Current", role: "admin" }),
    acknowledged: aCollectionPermission({ username: "Server", role: "admin" }),
    initial: "maker:edit",
  },
  {
    label: "printer",
    Editor: PrinterEditor,
    path: "/api/v1/printers/4/permissions",
    key: accessKeys.printer(1, 4),
    base: aPrinterPermission(),
    current: aPrinterPermission({ username: "Current", role: "admin" }),
    acknowledged: aPrinterPermission({ username: "Server", role: "admin" }),
    initial: "maker:print",
  },
];
afterEach(() => vi.restoreAllMocks());
describe.each(resources)(
  "$label direct access ownership",
  ({ Editor, path, key, base, current, acknowledged, initial }) => {
    function renderEditor() {
      return renderApp(<Editor />, {
        routes: {
          [`GET ${path}`]: json([{ ...base }]),
          [`PUT ${path}/2`]: json({ ...acknowledged }),
          [`DELETE ${path}/2`]: json(null, 204),
        },
      });
    }
    it("publishes the authoritative grant without another GET", async () => {
      const app = renderEditor();
      await screen.findByText(initial);
      await userEvent.click(screen.getByRole("button", { name: "Grant" }));
      expect(await screen.findByText("Server:admin")).toBeVisible();
      expect(app.client.getQueryData(key)).toEqual([acknowledged]);
      expect(app.requestsWithMethod("GET")).toHaveLength(1);
      expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toEqual({ role: "view" });
    });
    it("removes an acknowledged grant without another GET", async () => {
      const app = renderEditor();
      await screen.findByText(initial);
      await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
      await waitFor(() => expect(screen.queryByText(initial)).toBeNull());
      expect(app.client.getQueryData(key)).toEqual([]);
      expect(app.requestsWithMethod("GET")).toHaveLength(1);
      expect(app.requestsWithMethod("DELETE")).toHaveLength(1);
    });
    it("never dispatches a retired permission gesture", async () => {
      const app = renderEditor();
      await screen.findByText(initial);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Grant" }));
        app.unmount();
        clearLogin();
      });
      expect(app.requestsWithMethod("PUT")).toHaveLength(0);
      expect(app.client.getQueryData(key)).toBeUndefined();
    });
    it("discards a permission acknowledgement during retirement", async () => {
      const app = renderEditor();
      await screen.findByText(initial);
      const paused = Promise.withResolvers<void>();
      const cancel = app.client.cancelQueries.bind(app.client);
      const spy = vi
        .spyOn(app.client, "cancelQueries")
        .mockImplementationOnce(cancel)
        .mockImplementationOnce(() => paused.promise);
      await userEvent.click(screen.getByRole("button", { name: "Grant" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
      await act(async () => {
        app.unmount();
        clearLogin();
        paused.resolve();
        await paused.promise;
      });
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
      expect(app.client.getQueryData(key)).toBeUndefined();
    });
    it("leaves a new same-user permission read active after a retired ACK", async () => {
      const app = renderEditor();
      await screen.findByText(initial);
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
        await userEvent.click(screen.getByRole("button", { name: "Grant" }));
        await entered.promise;
        app.unmount();
        clearLogin();
        renderApp(<Editor />, {
          routes: {
            [`GET ${path}`]: (_url, init) => {
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
          currentRead.resolve(json([{ ...current }]));
        });
        expect(await screen.findByText("Current:admin")).toBeVisible();
      } finally {
        cache.config.onSuccess = previousSuccess;
        cache.config.onSettled = previousSettled;
        resume.resolve();
        currentRead.resolve(json([{ ...base }]));
      }
    });
  },
);
