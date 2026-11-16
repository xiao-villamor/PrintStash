/** Sidebar queries refine the current Library instead of creating a Search destination. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LibrarySearch } from "@/components/library-search";
import { usePathname, useSearchParams } from "@/lib/navigation";
import { json, renderApp } from "@/test-support/render";
import { searchStatus } from "@/test-support/search";

function Location() {
  return (
    <output data-testid="location">
      {usePathname()}?{useSearchParams().toString()}
    </output>
  );
}
function setup(at = "/?favorites=true&tag=useful") {
  renderApp(
    <>
      <LibrarySearch placement="sidebar" recent={["bracket"]} />
      <Location />
    </>,
    {
      at,
      routes: { "GET /api/v1/search/status": json(searchStatus()) },
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
