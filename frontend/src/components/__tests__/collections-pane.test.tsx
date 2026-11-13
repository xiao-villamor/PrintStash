/** Shell geometry preserves the Library tree owner and uses real outliner data on other routes. */
import { useState } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import {
  CollectionsPaneProvider,
  CollectionsPaneHost,
  LibraryCollectionsPane,
  LibraryBreadcrumbs,
} from "@/components/collections-pane";
import { useCollectionsPane } from "@/lib/collections-pane-context";
import { useRouter } from "@/lib/navigation";
import {
  adminSession,
  memberSession,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";
import { aCollection } from "@/test-support/factories";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { outlinerRoutes } from "@/test-support/outliner";

function Controls() {
  const pane = useCollectionsPane();
  const navigation = useRouter();
  const location = useLocation();
  return (
    <>
      <output aria-label="Pane state">{pane?.open ? "open" : "closed"}</output>
      <output aria-label="Location">{location.href}</output>
      <button onClick={pane?.toggle}>Toggle pane</button>
      <button onClick={() => navigation.push("/models/1")}>Open detail</button>
      <div ref={pane?.attachBreadcrumbs} aria-label="Topbar breadcrumbs" />
    </>
  );
}

function OwnedTree() {
  const [value, setValue] = useState("");
  return (
    <label>
      Owned tree filter
      <input value={value} onChange={(event) => setValue(event.target.value)} />
    </label>
  );
}

function renderPane(options: RenderAppOptions = {}, owned = false) {
  return renderApp(
    <CollectionsPaneProvider>
      <Controls />
      <CollectionsPaneHost />
      {owned && (
        <LibraryCollectionsPane>
          <OwnedTree />
        </LibraryCollectionsPane>
      )}
      <LibraryBreadcrumbs>
        <p>Confirmed collection breadcrumb</p>
      </LibraryBreadcrumbs>
    </CollectionsPaneProvider>,
    options,
  );
}

describe("CollectionsPaneProvider", () => {
  it.each([
    { at: "/", expected: "open" },
    { at: "/search", expected: "open" },
    { at: "/models/1", expected: "closed" },
    { at: "/printers", expected: "closed" },
  ])("uses the reference default at $at", ({ at, expected }) => {
    renderPane({ at });

    expect(screen.getByLabelText("Pane state")).toHaveTextContent(expected);
  });

  it("retains an explicit preference across route changes", async () => {
    const user = userEvent.setup();
    renderPane();

    await user.click(screen.getByRole("button", { name: "Toggle pane" }));
    await user.click(screen.getByRole("button", { name: "Open detail" }));

    expect(screen.getByLabelText("Pane state")).toHaveTextContent("closed");
    expect(localStorage.getItem("printstash.collections-pane.1")).toBe("closed");
  });

  it("keeps another user's pane preference separate", () => {
    localStorage.setItem("printstash.collections-pane.1", "open");
    renderPane({ at: "/models/1", auth: memberSession() });

    expect(screen.getByLabelText("Pane state")).toHaveTextContent("closed");
  });

  it("uses a saved collapsed preference on direct Library entry", () => {
    localStorage.setItem("printstash.collections-pane.1", "closed");
    renderPane({ at: "/", auth: adminSession() });

    expect(screen.getByLabelText("Pane state")).toHaveTextContent("closed");
  });
});

describe("LibraryCollectionsPane", () => {
  it("keeps owned tree state while the shell pane is collapsed", async () => {
    const user = userEvent.setup();
    renderPane({}, true);
    const input = screen.getByRole("textbox", { name: "Owned tree filter" });
    await user.type(input, "Hardware");

    await user.click(screen.getByRole("button", { name: "Toggle pane" }));
    await user.click(screen.getByRole("button", { name: "Toggle pane" }));

    expect(screen.getByRole("textbox", { name: "Owned tree filter" })).toBe(input);
    expect(input).toHaveValue("Hardware");
    expect(document.querySelector("#collections-pane")).toContainElement(input);
  });
});

describe("LibraryBreadcrumbs", () => {
  it("places confirmed feature breadcrumbs in the topbar host", () => {
    renderPane();

    expect(screen.getByLabelText("Topbar breadcrumbs")).toContainElement(
      screen.getByText("Confirmed collection breadcrumb"),
    );
  });
});

describe("CollectionsPaneHost", () => {
  it("remembers a submitted search in its actual panel", async () => {
    renderPane({ at: "/search?q=bracket" });
    expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveValue("bracket");
    expect(await screen.findByRole("button", { name: "bracket" })).toBeVisible();
    expect(JSON.parse(localStorage.getItem("printstash.recent-searches.1") ?? "[]")).toEqual([
      "bracket",
    ]);
  });
  it("bounds recent searches to eight distinct queries", () => {
    localStorage.setItem(
      "printstash.recent-searches.1",
      JSON.stringify(Array.from({ length: 8 }, (_, i) => `query${i}`)),
    );
    renderPane({ at: "/search?q=newest" });
    const panel = screen.getByRole("complementary", { name: "Search panel" });
    expect(within(panel).getAllByRole("button")).toHaveLength(8);
    expect(within(panel).queryByRole("button", { name: "query7" })).not.toBeInTheDocument();
  });
  it("keeps another account's recent queries private", () => {
    localStorage.setItem("printstash.recent-searches.1", JSON.stringify(["private query"]));
    renderPane({ at: "/search", auth: memberSession() });
    expect(screen.queryByRole("button", { name: "private query" })).not.toBeInTheDocument();
  });
  it("rejects malformed persisted recent queries", () => {
    localStorage.setItem(
      "printstash.recent-searches.1",
      JSON.stringify([42, null, { query: "wrong" }, "valid"]),
    );
    renderPane({ at: "/search" });
    const panel = screen.getByRole("complementary", { name: "Search panel" });
    expect(within(panel).getAllByRole("button")).toHaveLength(1);
    expect(within(panel).getByRole("button", { name: "valid" })).toBeVisible();
  });
  it("navigates from a secondary screen using the production outliner", async () => {
    const user = userEvent.setup();
    const collection = aCollection({ id: 5, name: "Hardware", path: "hardware" });
    const app = renderPane({
      at: "/inbox",
      routes: { ...collectionTreeRoutes([collection]), ...outlinerRoutes([collection]) },
    });

    await user.click(await screen.findByRole("treeitem", { name: "Hardware" }));

    await waitFor(() =>
      expect(
        new URL(
          screen.getByLabelText("Location").textContent!,
          "https://printstash.example",
        ).searchParams.get("c"),
      ).toBe("hardware"),
    );
    expect(
      app.requests().filter((request) => request.url.includes("/outliner/collections")),
    ).toHaveLength(1);
  });

  it("routes collection creation back to the existing Library command owner", async () => {
    const user = userEvent.setup();
    renderPane({ at: "/inbox" });

    await user.click(screen.getByRole("button", { name: "New collection" }));

    await waitFor(() =>
      expect(
        new URL(
          screen.getByLabelText("Location").textContent!,
          "https://printstash.example",
        ).searchParams.get("newCollection"),
      ).toBe("1"),
    );
  });
});
