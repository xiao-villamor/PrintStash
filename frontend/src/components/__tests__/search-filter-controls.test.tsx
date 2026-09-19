/** Submitted queries become editable canonical state; later edits never trigger another parser call. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLocation } from "react-router-dom";
import SearchPage from "@/pages/search";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import { aSearchResult, searchResponse, searchStatus } from "@/test-support/search";
import type { ParsedSearch, SearchPreferences } from "@/types/search";

const preference: SearchPreferences = {
  available: true,
  nl_filters_enabled: true,
  timezone: null,
  effective_timezone: "Europe/Madrid",
  endpoint_host: "local-chat:8080",
};
const parsed: ParsedSearch = {
  parsed: true,
  reason: null,
  timezone: "Europe/Madrid",
  residual_query: "bracket",
  sort: "printed-desc",
  filters: {
    direct: false,
    favorites: false,
    tag: [],
    printed: true,
    print_outcome: ["completed"],
    printed_after: "2026-08-01T00:00:00Z",
    printed_before: "2026-09-01T00:00:00Z",
    print_duration_max_s: 10800,
  },
};
function Location() {
  return <span data-testid="location">{useLocation().search}</span>;
}
function setup(options: RenderAppOptions = {}) {
  return renderApp(
    <>
      <SearchPage />
      <Location />
    </>,
    {
      at: "/search?q=brackets+printed+last+month+under+3+hours&parse=1",
      ...options,
      routes: {
        "GET /api/v1/search/status": json(searchStatus()),
        "GET /api/v1/search/preferences": json(preference),
        "GET /api/v1/saved-views": json([]),
        "POST /api/v1/search/parse": json(parsed),
        "GET /api/v1/search?": json(
          searchResponse({ items: [aSearchResult()], outcome: "results" }),
        ),
        ...options.routes,
      },
    },
  );
}
async function waitForParsedSearch() {
  // Observe the completed navigation before traversing the large result tree.
  // A recorded POST only proves that parsing started.
  await waitFor(() =>
    expect(new URLSearchParams(screen.getByTestId("location").textContent ?? "").get("q")).toBe(
      "bracket",
    ),
  );
}
describe("Natural-language search filters", () => {
  it("turns a submitted sentence into canonical editable filters", async () => {
    const app = setup();
    await waitForParsedSearch();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    expect(await screen.findByRole("button", { name: "Remove Successful print" })).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent("q=bracket");
    expect(screen.getByTestId("location")).not.toHaveTextContent("parse=1");
    expect(screen.getByTestId("location")).not.toHaveTextContent("last+month");
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.includes("/search?q="))).toBe(true),
    );
    const request = app.requests().find((request) => request.url.includes("/search?q="));
    const params = new URL(request?.url ?? "", "http://test").searchParams;
    expect(params.get("q")).toBe("bracket");
    expect(params.get("sort")).toBe("printed-desc");
    expect(JSON.parse(params.get("filters") ?? "{}")).toMatchObject({
      print_duration_max_s: 10800,
      print_outcome: ["completed"],
    });
  });
  it("removes only the chosen filter without parsing again", async () => {
    const app = setup();
    const user = userEvent.setup();
    await waitForParsedSearch();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    await user.click(await screen.findByRole("button", { name: "Remove Successful print" }));
    expect(screen.queryByRole("button", { name: "Remove Successful print" })).toBeNull();
    expect(screen.getByTestId("location")).toHaveTextContent("print_duration_max_s=10800");
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("edits an inferred duration without parsing again", async () => {
    const app = setup();
    const user = userEvent.setup();
    await waitForParsedSearch();
    await user.click(
      await screen.findByRole("button", { name: "Edit Actual duration < seconds: 10800" }),
    );
    const input = screen.getByRole("textbox", { name: "Actual duration < seconds" });
    await user.clear(input);
    await user.type(input, "7200");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(screen.getByTestId("location")).toHaveTextContent("print_duration_max_s=7200");
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it.each([false, true])(
    "keeps opt-out searches local when availability is %s",
    async (available) => {
      const app = setup({
        routes: {
          "GET /api/v1/search/preferences": json({
            ...preference,
            available,
            nl_filters_enabled: false,
          }),
        },
      });
      expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
      expect(app.requestsWithMethod("POST")).toHaveLength(0);
      expect(screen.getByTestId("location")).toHaveTextContent("last+month");
      expect(screen.getByTestId("location")).not.toHaveTextContent("parse=1");
    },
  );
  it("falls back to the original search on parser failure", async () => {
    const app = setup({ routes: { "POST /api/v1/search/parse": json({}, 503) } });
    expect(
      await screen.findByText("Could not interpret the filters. Searching your original text."),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        app.requests().some((request) => request.url.includes("q=brackets+printed+last+month")),
      ).toBe(true),
    );
    expect(screen.queryByRole("button", { name: "Remove Successful print" })).toBeNull();
  });
  it("loads filter-only URLs without parsing", async () => {
    const app = setup({ at: "/search?print_duration_max_s=10800&print_outcome=completed" });
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("saves normalized search state", async () => {
    const app = setup({
      routes: {
        "POST /api/v1/saved-views": json({ id: 42, name: "My brackets", filters: parsed.filters }),
      },
    });
    const user = userEvent.setup();
    await waitForParsedSearch();
    await user.click(screen.getByText("Search options"));
    await user.click(screen.getByRole("button", { name: "Saved views" }));
    await user.click(screen.getByRole("button", { name: /Save current view/ }));
    await user.type(screen.getByRole("textbox", { name: "View name" }), "My brackets");
    await user.click(screen.getByRole("button", { name: "Save view" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(2));
    const savedBody =
      app.requestsWithMethod("POST").find((request) => request.url.endsWith("/saved-views"))
        ?.body ?? "";
    expect(JSON.parse(savedBody)).toMatchObject({
      name: "My brackets",
      filters: { q: "bracket", sort: "printed-desc", print_duration_max_s: 10800 },
    });
    expect(savedBody).not.toContain("last month");
  });
  it("uses Spanish for inferred print-history filters", async () => {
    setup({ locale: "es" });
    await waitForParsedSearch();
    expect(await screen.findByRole("button", { name: "Quitar Impresión correcta" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Filtros del historial de impresión" }),
    ).toBeVisible();
  });
});
