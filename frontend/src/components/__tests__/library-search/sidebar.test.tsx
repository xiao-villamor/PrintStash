/** Sidebar queries refine the current Library instead of creating a Search destination. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LibrarySearch } from "@/components/library-search";
import { usePathname, useSearchParams } from "@/lib/navigation";
import { json, renderApp } from "@/test-support/render";
import { searchStatus } from "@/test-support/search";
import { aCollectionNode } from "@/test-support/factories";

function Location() {
  return (
    <output data-testid="location">
      {usePathname()}?{useSearchParams().toString()}
    </output>
  );
}
const partsLookup = {
  collection: aCollectionNode({ id: 4, name: "Shop parts", path: "parts" }),
  ancestors: [],
};
function setup(at = "/?favorites=true&tag=useful") {
  renderApp(
    <>
      <LibrarySearch placement="sidebar" recent={["bracket"]} />
      <Location />
    </>,
    {
      at,
      routes: {
        "GET /api/v1/search/status": json(searchStatus()),
        "GET /api/v1/collections/lookup": json(partsLookup),
      },
    },
  );
}

describe("Library sidebar search", () => {
  it("preserves Library filters throughout the query lifecycle", async () => {
    setup();
    const user = userEvent.setup();
    await user.type(screen.getByRole("searchbox"), "bracket");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/?favorites=true&tag=useful&q=bracket",
      ),
    );
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/?favorites=true&tag=useful&q=bracket",
    );
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/?favorites=true&tag=useful");
    expect(screen.getByRole("searchbox")).toHaveFocus();
  });
  it("applies a recent query to the same global results", async () => {
    setup();
    await userEvent.setup().click(screen.getByRole("button", { name: /^bracket$/ }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/?favorites=true&tag=useful&q=bracket",
      ),
    );
    expect(screen.getByRole("searchbox")).toHaveValue("bracket");
  });
});

describe("Library sidebar search scope", () => {
  it("names the collection being searched", async () => {
    setup("/?c=parts&cid=4&q=bracket");
    const scope = screen.getByRole("group", { name: "Search scope" });
    expect(await within(scope).findByRole("button", { name: "Shop parts" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("Find names, tags and file names in Shop parts.")).toBeVisible();
  });

  it("widens a collection search to the whole library", async () => {
    setup("/?c=parts&cid=4&scope=here&q=bracket");
    await userEvent.setup().click(screen.getByRole("button", { name: "Whole library" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/\?q=bracket$/),
    );
  });

  it("returns a widened search to the collection it came from", async () => {
    setup("/?c=parts&cid=4&q=bracket");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Whole library" }));
    await user.click(await screen.findByRole("button", { name: "Shop parts" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/?q=bracket&c=parts&cid=4"),
    );
  });

  it("omits the scope switch outside a collection", () => {
    setup("/?q=bracket");
    expect(screen.queryByRole("group", { name: "Search scope" })).not.toBeInTheDocument();
  });
});
