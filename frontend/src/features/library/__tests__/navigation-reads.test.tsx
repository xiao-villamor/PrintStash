/** Explicit navigation begins its real Query reads before the destination mounts. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLibraryNavigationReads } from "../navigation-reads";
import { libraryBrowseKeys, useLibraryBrowse } from "../browse";
import { useCollectionLookup } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { retirePrivateSessionScope } from "@/lib/auth-store";
import { aCollection, aModelListItem } from "@/test-support/factories";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import type { LibraryBrowseParams } from "@/types/library-browse";

const params = (path: string): LibraryBrowseParams => ({
  collection: path,
  direct: true,
  view: "all",
  limit: 24,
  sort: "date-desc",
});
const page = () =>
  json({
    items: [{ kind: "model", model: aModelListItem({ name: "Destination Model" }) }],
    next_cursor: null,
    total: 1,
    browse_revision: "1",
    authorization_revision: "1",
  });
function Destination() {
  const browse = useLibraryBrowse(params("parts"));
  const lookup = useCollectionLookup("parts");
  return (
    <>
      <p>{lookup.data?.collection.name}</p>
      <p>
        {browse.data?.pages[0].items
          .map((item) => (item.kind === "model" ? item.model.name : item.multipart.name))
          .join(",")}
      </p>
    </>
  );
}
function Probe() {
  const start = useLibraryNavigationReads();
  const [mounted, setMounted] = useState(false);
  return (
    <>
      <button onClick={() => start("parts", params("parts"))}>Choose parts</button>
      <button onClick={() => start("tools", params("tools"))}>Choose tools</button>
      <button onClick={() => setMounted(true)}>Mount destination</button>
      {mounted ? <Destination /> : <p>Previous destination</p>}
    </>
  );
}
function renderProbe(routes: RouteTable = {}) {
  return renderApp(<Probe />, {
    routes: {
      ...collectionTreeRoutes([
        aCollection({ id: 1, name: "Parts", path: "parts" }),
        aCollection({ id: 2, name: "Tools", path: "tools" }),
      ]),
      "GET /api/v1/models/browse": page,
      ...routes,
    },
  });
}

describe("useLibraryNavigationReads", () => {
  it("starts the selected destination before its view mounts", async () => {
    const app = renderProbe();
    await userEvent.click(screen.getByRole("button", { name: "Choose parts" }));
    await waitFor(() => expect(app.client.isFetching()).toBe(0));
    expect(screen.getByText("Previous destination")).toBeVisible();
    expect(app.requests().map((request) => request.url.split("?")[0])).toEqual([
      "/api/v1/models/browse",
      "/api/v1/collections/lookup",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Mount destination" }));
    expect(await screen.findByText("Destination Model")).toBeVisible();
    expect(screen.getByText("Parts")).toBeVisible();
    expect(app.requests()).toHaveLength(2);
  });

  it("deduplicates repeated selection before the destination mounts", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderProbe({ "GET /api/v1/models/browse": () => pending.promise });
    await userEvent.click(screen.getByRole("button", { name: "Choose parts" }));
    await userEvent.click(screen.getByRole("button", { name: "Choose parts" }));
    expect(
      app.requests().filter((request) => request.url.startsWith("/api/v1/models/browse")),
    ).toHaveLength(1);
    await act(async () => pending.resolve(page()));
    await userEvent.click(screen.getByRole("button", { name: "Mount destination" }));
    expect(await screen.findByText("Destination Model")).toBeVisible();
  });

  it("cancels an unmounted destination when a newer one is selected", async () => {
    const pending = Promise.withResolvers<Response>();
    const signals: AbortSignal[] = [];
    const app = renderProbe({
      "GET /api/v1/models/browse": (url, init) => {
        if (new URL(url, "http://test").searchParams.get("collection") !== "parts") return page();
        if (!init?.signal) throw new Error("Missing read cancellation");
        signals.push(init.signal);
        return pending.promise;
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Choose parts" }));
    await waitFor(() => expect(signals).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "Choose tools" }));
    expect(signals[0].aborted).toBe(true);
    await act(async () => pending.resolve(page()));
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params("parts")))).toBeUndefined();
    await waitFor(() =>
      expect(app.client.getQueryData(libraryBrowseKeys.pages(params("tools")))).toBeDefined(),
    );
  });

  it("cancels unread work when the library unmounts", async () => {
    const pending = Promise.withResolvers<Response>();
    const signals: AbortSignal[] = [];
    const hold = (_url: string, init?: RequestInit) => {
      if (!init?.signal) throw new Error("Missing read cancellation");
      signals.push(init.signal);
      return pending.promise;
    };
    const app = renderProbe({
      "GET /api/v1/models/browse": hold,
      "GET /api/v1/collections/lookup": hold,
    });
    await userEvent.click(screen.getByRole("button", { name: "Choose parts" }));
    await waitFor(() => expect(signals).toHaveLength(2));
    app.unmount();
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    pending.resolve(page());
  });

  it("discards destination reads from a retired session", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderProbe({ "GET /api/v1/models/browse": () => pending.promise });
    await userEvent.click(screen.getByRole("button", { name: "Choose parts" }));
    act(() => retirePrivateSessionScope());
    await act(async () => pending.resolve(page()));
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params("parts")))).toBeUndefined();
    expect(app.client.getQueryData(queryKeys.collectionLookup("parts"))).toBeUndefined();
  });
});
