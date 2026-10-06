/** Ordered pages retain their sequence and remain usable when continuation fails. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLibraryBrowse } from "../browse";
import { aModelListItem, aMultipartModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

function Probe({ limit = 24 }: { limit?: number }) {
  const query = useLibraryBrowse({ view: "all", sort: "name-asc", limit });
  return (
    <>
      <output>
        {query.data?.pages
          .flatMap((page) => page.items)
          .map((entry) => (entry.kind === "model" ? entry.model.name : entry.multipart.name))
          .join(",")}
      </output>
      <button onClick={() => void query.loadMore()}>More</button>
      {query.refreshRequired && <p>Refresh required</p>}
    </>
  );
}

describe("useLibraryBrowse", () => {
  it("appends the server sequence unchanged", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": (url) =>
          json(
            new URL(url, "http://test").searchParams.has("cursor")
              ? {
                  items: [{ kind: "model", model: aModelListItem({ name: "Älpha" }) }],
                  next_cursor: null,
                  total: 2,
                  browse_revision: "1",
                  authorization_revision: "1",
                }
              : {
                  items: [{ kind: "multipart", multipart: aMultipartModel({ name: "Zeta" }) }],
                  next_cursor: "next",
                  total: 2,
                  browse_revision: "1",
                  authorization_revision: "1",
                },
          ),
      },
    });
    await screen.findByText("Zeta");

    await userEvent.click(screen.getByRole("button", { name: "More" }));

    expect(await screen.findByText("Zeta,Älpha")).toBeVisible();
  });

  it("continues after an empty page", async () => {
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": (url) =>
          json(
            new URL(url, "http://test").searchParams.has("cursor")
              ? {
                  items: [{ kind: "model", model: aModelListItem({ name: "Reached" }) }],
                  next_cursor: null,
                  total: 1,
                  browse_revision: "1",
                  authorization_revision: "1",
                }
              : {
                  items: [],
                  next_cursor: "next",
                  total: 1,
                  browse_revision: "1",
                  authorization_revision: "1",
                },
          ),
      },
    });
    await waitFor(() => expect(app.client.isFetching()).toBe(0));

    await userEvent.click(screen.getByRole("button", { name: "More" }));

    expect(await screen.findByText("Reached")).toBeVisible();
  });

  it("retains displayed pages when continuation requires refresh", async () => {
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": (url) =>
          new URL(url, "http://test").searchParams.has("cursor")
            ? json({ detail: "browse_refresh_required" }, 409)
            : json({
                items: [{ kind: "model", model: aModelListItem({ name: "Retained" }) }],
                next_cursor: "next",
                total: 2,
                browse_revision: "1",
                authorization_revision: "1",
              }),
      },
    });
    await screen.findByText("Retained");

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await screen.findByText("Refresh required");
    await userEvent.click(screen.getByRole("button", { name: "More" }));

    expect(screen.getByRole("status")).toHaveTextContent("Retained");
    expect(app.requests().filter((request) => request.url.includes("cursor=next"))).toHaveLength(1);
  });

  it("keeps one continuation in flight", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": (url, init) => {
          if (new URL(url, "http://test").searchParams.has("cursor")) {
            signal = init?.signal;
            return pending.promise;
          }
          return json({
            items: [{ kind: "model", model: aModelListItem({ name: "First" }) }],
            next_cursor: "next",
            total: 2,
            browse_revision: "1",
            authorization_revision: "1",
          });
        },
      },
    });
    await screen.findByText("First");

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(screen.getByRole("button", { name: "More" }));

    expect(app.requests().filter((request) => request.url.includes("cursor=next"))).toHaveLength(1);
    expect(signal?.aborted).toBe(false);
    await act(async () =>
      pending.resolve(
        json({
          items: [{ kind: "model", model: aModelListItem({ name: "Second" }) }],
          next_cursor: null,
          total: 2,
          browse_revision: "1",
          authorization_revision: "1",
        }),
      ),
    );
    expect(await screen.findByText("First,Second")).toBeVisible();
  });

  it("isolates page sizes in the cache", async () => {
    renderApp(
      <>
        <Probe limit={1} />
        <Probe limit={2} />
      </>,
      {
        routes: {
          "GET /api/v1/models/browse": (url) =>
            json({
              items: [
                {
                  kind: "model",
                  model: aModelListItem({
                    name: `Page size ${new URL(url, "http://test").searchParams.get("limit")}`,
                  }),
                },
              ],
              next_cursor: null,
              total: 1,
              browse_revision: "1",
              authorization_revision: "1",
            }),
        },
      },
    );

    expect(await screen.findByText("Page size 1")).toBeVisible();
    expect(await screen.findByText("Page size 2")).toBeVisible();
  });
});
