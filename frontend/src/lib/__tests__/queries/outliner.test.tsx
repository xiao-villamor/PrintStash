/** Filter changes must cancel old sidebar requests before they can replace visible rows. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useOutlinerEntries } from "@/lib/queries";
import { json, renderApp } from "@/test-support/render";

describe("outliner query isolation", () => {
  it("isolates late responses from obsolete filters", async () => {
    const pending = Promise.withResolvers<Response>();
    let obsoleteSignal: AbortSignal | null | undefined;
    function Probe() {
      const [favorites, setFavorites] = useState(false);
      const query = useOutlinerEntries({ view: "all", favorites });
      return (
        <>
          <button onClick={() => setFavorites(true)}>Favorites only</button>
          <output>
            {query.data?.pages
              .flatMap((p) => p.items)
              .map((item) => item.name)
              .join(",")}
          </output>
        </>
      );
    }
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/outliner/entries": (url, init) => {
          if (new URL(url, "http://test").searchParams.get("favorites") === "true")
            return json({
              items: [
                {
                  kind: "model",
                  id: 2,
                  name: "Current favorite",
                  collection: null,
                  collection_id: null,
                  collection_label: null,
                },
              ],
              next_cursor: null,
            });
          obsoleteSignal = init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() => expect(obsoleteSignal).toBeDefined());
    await userEvent.click(screen.getByRole("button", { name: "Favorites only" }));
    await screen.findByText("Current favorite");
    expect(obsoleteSignal?.aborted).toBe(true);
    pending.resolve(
      json({ items: [{ kind: "model", id: 1, name: "Obsolete item" }], next_cursor: null }),
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Current favorite"));
    expect(screen.queryByText("Obsolete item")).not.toBeInTheDocument();
  });
});
