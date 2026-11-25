/** Library filtering stays local to the view; AI navigation requires an explicit action. */
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import SearchPage from "@/pages/search";
import { Outlet } from "@tanstack/react-router";
import { LibrarySearch } from "@/components/library-search";
import { usePathname, useSearchParams } from "@/lib/navigation";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import { aSearchResult, searchResponse, searchStatus } from "@/test-support/search";

function Location() {
  return (
    <output data-testid="location">
      {usePathname()}?{useSearchParams().toString()}
    </output>
  );
}
function searchBox(options: RenderAppOptions = {}, placement: "header" | "sidebar" = "header") {
  return renderApp(
    <>
      <LibrarySearch placement={placement} />
      <Location />
    </>,
    {
      ...options,
      routes: {
        "GET /api/v1/search/status": json(searchStatus()),
        "GET /api/v1/search?": json(searchResponse({ items: [aSearchResult()] })),
        ...options.routes,
      },
    },
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("LibrarySearch", () => {
  it("does not spellcheck model names in the search field", () => {
    searchBox();
    expect(screen.getByRole("searchbox")).toHaveAttribute("spellcheck", "false");
  });
  it("clears the submitted search in the results view", async () => {
    const user = userEvent.setup();
    searchBox({ at: "/search?q=bracket&parse=1" });
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/search?mode=lexical"),
    );
    expect(screen.getByRole("searchbox")).toHaveValue("");
    expect(screen.getByRole("searchbox")).toHaveFocus();
  });
  it("clears only the library query", async () => {
    searchBox({ at: "/?c=Tools&tag=useful&sort=name_asc&q=boat" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/?c=Tools&tag=useful&sort=name_asc");
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
  it("clears the result query through keyboard editing", async () => {
    const user = userEvent.setup();
    searchBox({ at: "/search?q=bracket" });
    await user.clear(screen.getByRole("searchbox"));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/search?mode=lexical"),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("discards a late response after clearing search", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(
      <>
        <LibrarySearch />
        <Outlet />
      </>,
      {
        at: "/search?q=bracket",
        routerRoutes: [
          { path: "/", element: <h1>Library</h1> },
          { path: "/search", element: <SearchPage /> },
        ],
        routes: {
          "GET /api/v1/search?": () => pending.promise.then((response) => response.clone()),
          "GET /api/v1/search/status": json(searchStatus()),
          "GET /api/v1/saved-views": json([]),
          "GET /api/v1/search/availability": json({ available: false, nl_filters_enabled: false }),
        },
      },
    );
    await user.type(screen.getByRole("searchbox"), " new{Enter}");
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.includes("/search?"))).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByRole("searchbox")).toHaveValue("");
    await act(async () => pending.resolve(json(searchResponse({ items: [aSearchResult()] }))));
    expect(screen.queryByRole("link", { name: "Desk bracket" })).toBeNull();
    expect(screen.queryByText("Searching…")).toBeNull();
  });
  it("debounces keyword filtering in the Library", async () => {
    const user = userEvent.setup();
    const app = searchBox();
    await user.type(screen.getByRole("searchbox"), "bracket");

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/?q=bracket"));
    expect(
      app.requests().filter((request) => request.url.startsWith("/api/v1/search?")),
    ).toHaveLength(0);
  });
  it("keeps Enter in the library with existing filters", async () => {
    const user = userEvent.setup();
    searchBox({ at: "/?c=Tools&favorite=true" });
    await user.type(screen.getByRole("searchbox"), "small boat{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/?c=Tools&favorite=true&q=small+boat",
    );
    await new Promise((resolve) => window.setTimeout(resolve, 300));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/?c=Tools&favorite=true&q=small+boat",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("opens AI results from the search bar", async () => {
    const user = userEvent.setup();
    searchBox({
      routes: {
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
      },
    });
    await user.type(screen.getByRole("searchbox"), "small boat");
    const ai = await screen.findByRole("button", { name: "AI Search" });
    expect(ai).toHaveAttribute("aria-pressed", "false");
    expect(ai).toHaveTextContent("AI Search");
    await user.click(ai);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/search?q=small+boat&mode=hybrid&parse=1",
    );
    expect(screen.getByRole("button", { name: "AI Search" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
  it("shows the AI control when search is enabled before a query", async () => {
    searchBox({
      routes: {
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
      },
    });
    expect(await screen.findByRole("button", { name: "AI Search" })).toBeEnabled();
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
  it("shows unavailable AI without allowing an AI search", async () => {
    searchBox({
      routes: {
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: false })),
      },
    });
    await userEvent.setup().type(screen.getByRole("searchbox"), "bracket");
    expect(await screen.findByRole("button", { name: "AI Search" })).toBeDisabled();
  });
  it("disables cached capabilities after a failed refresh", async () => {
    const app = searchBox({
      routes: {
        "GET /api/v1/search/status": json(
          searchStatus({ enabled: true, semantic_ready: true, legs: ["thumbnail"] }),
        ),
      },
    });
    await userEvent.type(screen.getByRole("searchbox"), "bracket");
    expect(await screen.findByRole("button", { name: "AI Search" })).toBeEnabled();
    app.route({ "GET /api/v1/search/status": json({}, 503) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["ai-search", "status"] });
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: "AI Search" })).toBeNull());
    expect(screen.queryByRole("button", { name: "Search by image" })).toBeNull();
    expect(screen.getByRole("searchbox")).toHaveValue("bracket");
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent("/?q=bracket");
  });
  it("hides the AI control when AI search is disabled", async () => {
    searchBox();
    await userEvent.setup().type(screen.getByRole("searchbox"), "bracket");
    expect(screen.queryByRole("button", { name: /AI search|Search with AI/ })).toBeNull();
  });
  it("switches active AI results to keyword results", async () => {
    const user = userEvent.setup();
    searchBox({
      at: "/search?q=bracket",
      routes: {
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
      },
    });
    const ai = await screen.findByRole("button", { name: "AI Search" });
    expect(ai).toHaveAttribute("aria-pressed", "true");
    await user.click(ai);
    expect(screen.getByTestId("location")).toHaveTextContent("/search?q=bracket&mode=lexical");
    expect(screen.getByRole("button", { name: "AI Search" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
  it("switches keyword results back to AI", async () => {
    const user = userEvent.setup();
    searchBox({
      at: "/search?q=bracket&mode=lexical",
      routes: {
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
      },
    });
    await user.click(await screen.findByRole("button", { name: "AI Search" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/search?q=bracket&mode=hybrid&parse=1",
    );
    expect(screen.getByRole("button", { name: "AI Search" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
  it("preserves keyword mode when submitting another query", async () => {
    const user = userEvent.setup();
    searchBox({
      at: "/search?q=bracket&mode=lexical",
      routes: {
        "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
      },
    });
    const input = screen.getByRole("searchbox");
    await user.type(input, " boat{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent("/search?q=bracket+boat&mode=lexical");
  });
  it("keeps focus in the search field as the Library query updates", async () => {
    searchBox();
    const input = screen.getByRole("searchbox");
    await userEvent.type(input, "a/b");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/?q=a%2Fb"));
    expect(input).toHaveFocus();
  });

  it("supports Spanish sidebar search controls", async () => {
    searchBox(
      {
        locale: "es",
        routes: {
          "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
        },
      },
      "sidebar",
    );

    await userEvent.type(
      screen.getByRole("searchbox", { name: "Buscar en la biblioteca" }),
      "soporte",
    );
    expect(screen.getByRole("button", { name: "Búsqueda con IA" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/?q=soporte"));
  });

  it("recovers sidebar AI capability after a failed request", async () => {
    const app = searchBox({ routes: { "GET /api/v1/search/status": json({}, 503) } }, "sidebar");
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(screen.getByRole("button", { name: "AI Search" })).toBeDisabled();

    app.route({
      "GET /api/v1/search/status": json(searchStatus({ enabled: true, semantic_ready: true })),
    });
    await userEvent.click(retry);
    await waitFor(() => expect(screen.getByRole("button", { name: "AI Search" })).toBeEnabled());
  });
});
