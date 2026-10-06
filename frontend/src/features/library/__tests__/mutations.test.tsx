/** Confirmed library writes survive stale reads without leaking across sessions. */
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLibraryBrowse } from "../browse";
import { useLibraryStar } from "../mutations";
import { aModelListItem, aMultipartModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";
import type { LibraryBrowsePage } from "@/types/library-browse";

const PAGE: LibraryBrowsePage = {
  items: [{ kind: "model", model: aModelListItem({ id: 1, name: "Bracket", starred: true }) }],
  next_cursor: "next",
  total: 2,
  browse_revision: "r1",
  authorization_revision: "a1",
};

function Probe({
  favorites = false,
  kind = "model",
}: {
  favorites?: boolean;
  kind?: "model" | "multipart";
}) {
  const query = useLibraryBrowse({ view: "all", sort: "name-asc", limit: 24, favorites });
  const mutation = useLibraryStar();
  return (
    <>
      <output aria-label={favorites ? "Favorites" : "Everything"}>
        {query.data?.pages
          .flatMap((page) => page.items)
          .map((item) => {
            const value = item.kind === "model" ? item.model : item.multipart;
            return `${item.kind}:${value.name}:${value.starred}`;
          })
          .join(",")}
      </output>
      <button onClick={() => mutation.mutate({ kind: "model", id: 1, starred: false })}>
        Unstar
      </button>
      <button onClick={() => void query.loadMore()}>More</button>
      <button onClick={() => mutation.mutate({ kind, id: 1, starred: true })}>Star</button>
      {mutation.isSuccess && <p>Confirmed</p>}
      {mutation.isError && <p>Could not save</p>}
    </>
  );
}

describe("useLibraryStar", () => {
  it.each([
    {
      kind: "model" as const,
      page: {
        ...PAGE,
        items: [
          {
            kind: "model" as const,
            model: aModelListItem({ id: 1, name: "Bracket", starred: false }),
          },
        ],
      },
      expected: "model:Bracket:true",
    },
    {
      kind: "multipart" as const,
      page: {
        ...PAGE,
        items: [
          {
            kind: "multipart" as const,
            multipart: aMultipartModel({ id: 1, name: "Rig", starred: false }),
          },
        ],
      },
      expected: "multipart:Rig:true",
    },
  ])("stars either supported subject kind ($kind)", async ({ kind, page, expected }) => {
    const app = renderApp(<Probe kind={kind} />, {
      routes: {
        "GET /api/v1/models/browse": json(page),
        "PUT /api/v1/models/1/star": json({ model_id: 1, starred: true }),
        "PUT /api/v1/multipart-models/1/star": json({ multipart_model_id: 1, starred: true }),
      },
    });
    await waitFor(() => expect(app.client.isFetching()).toBe(0));

    await userEvent.click(screen.getByRole("button", { name: "Star" }));

    await screen.findByText("Confirmed");
    expect(screen.getByLabelText("Everything")).toHaveTextContent(expected);
  });

  it("rejects a queued gesture after session retirement", async () => {
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": json(PAGE),
        "DELETE /api/v1/models/1/star": json({ model_id: 1, starred: false }),
      },
    });
    await screen.findByText("model:Bracket:true");

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Unstar" }));
      clearLogin();
    });

    await screen.findByText("Could not save");
    expect(app.requestsWithMethod("DELETE")).toEqual([]);
  });

  it("publishes a confirmed star across loaded browse scopes", async () => {
    renderApp(
      <>
        <Probe />
        <Probe favorites />
      </>,
      {
        routes: {
          "GET /api/v1/models/browse": json(PAGE),
          "DELETE /api/v1/models/1/star": json({ model_id: 1, starred: false }),
        },
      },
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Everything")).toHaveTextContent("model:Bracket:true"),
    );

    await userEvent.click(screen.getAllByRole("button", { name: "Unstar" })[0]);

    await screen.findByText("Confirmed");
    expect(screen.getByLabelText("Everything")).toHaveTextContent("model:Bracket:false");
    expect(screen.getByLabelText("Favorites")).toBeEmptyDOMElement();
  });

  it("removes an unstarred favorite only after confirmation", async () => {
    const pending = Promise.withResolvers<Response>();
    renderApp(<Probe favorites />, {
      routes: {
        "GET /api/v1/models/browse": json(PAGE),
        "DELETE /api/v1/models/1/star": () => pending.promise,
      },
    });
    await screen.findByText("model:Bracket:true");

    await userEvent.click(screen.getByRole("button", { name: "Unstar" }));

    expect(screen.getByLabelText("Favorites")).toHaveTextContent("model:Bracket:true");
    await act(async () => pending.resolve(json({ model_id: 1, starred: false })));
    await screen.findByText("Confirmed");
    expect(screen.getByLabelText("Favorites")).toBeEmptyDOMElement();
  });

  it("keeps a favorite when unstar fails", async () => {
    renderApp(<Probe favorites />, {
      routes: {
        "GET /api/v1/models/browse": json(PAGE),
        "DELETE /api/v1/models/1/star": json({ detail: "write_failed" }, 500),
      },
    });
    await screen.findByText("model:Bracket:true");

    await userEvent.click(screen.getByRole("button", { name: "Unstar" }));

    await screen.findByText("Could not save");
    expect(screen.getByLabelText("Favorites")).toHaveTextContent("model:Bracket:true");
  });

  it("rejects a late page after a confirmed star", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": (url) =>
          url.includes("cursor=") ? pending.promise : json(PAGE),
        "DELETE /api/v1/models/1/star": json({ model_id: 1, starred: false }),
      },
    });
    await screen.findByText("model:Bracket:true");
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() =>
      expect(app.requests().some((call) => call.url.includes("cursor=next"))).toBe(true),
    );

    await userEvent.click(screen.getByRole("button", { name: "Unstar" }));
    await screen.findByText("Confirmed");
    await act(async () => pending.resolve(json({ ...PAGE, next_cursor: null })));

    expect(screen.getByLabelText("Everything")).toHaveTextContent(/^model:Bracket:false$/);
  });

  it("does not publish a star into another session", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": json(PAGE),
        "DELETE /api/v1/models/1/star": () => pending.promise,
      },
    });
    await screen.findByText("model:Bracket:true");
    await userEvent.click(screen.getByRole("button", { name: "Unstar" }));
    await waitFor(() => expect(app.requestsWithMethod("DELETE")).toHaveLength(1));

    act(() => clearLogin());
    await act(async () => pending.resolve(json({ model_id: 1, starred: false })));

    expect(screen.queryByText("Confirmed")).not.toBeInTheDocument();
    expect(app.client.getQueryData(["models", 1])).toBeUndefined();
    expect(screen.queryByText("model:Bracket:false")).not.toBeInTheDocument();
  });

  it("isolates equal numeric identifiers across subject kinds", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/browse": json({
          ...PAGE,
          items: [
            ...PAGE.items,
            {
              kind: "multipart",
              multipart: aMultipartModel({ id: 1, name: "Rig", starred: true }),
            },
          ],
        }),
        "DELETE /api/v1/models/1/star": json({ model_id: 1, starred: false }),
      },
    });
    await screen.findByText("model:Bracket:true,multipart:Rig:true");

    await userEvent.click(screen.getByRole("button", { name: "Unstar" }));

    await screen.findByText("Confirmed");
    expect(screen.getByLabelText("Everything")).toHaveTextContent(
      "model:Bracket:false,multipart:Rig:true",
    );
  });
});
