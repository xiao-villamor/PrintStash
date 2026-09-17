/** Search pages preserve authorization evidence, bounded pagination and query privacy. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { aModelListItem } from "@/test-support/factories";
import SearchPage from "@/pages/search";
import { AuthContext } from "@/lib/auth-context";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";
import { aSearchResult, searchResponse, searchStatus } from "@/test-support/search";

function results(options: RenderAppOptions = {}) {
  return renderApp(<SearchPage />, {
    at: "/search?q=bracket",
    ...options,
    routes: {
      "GET /api/v1/search/status": json(searchStatus()),
      "GET /api/v1/search?": json(searchResponse({ items: [aSearchResult()], outcome: "results" })),
      ...options.routes,
    },
  });
}
afterEach(() => vi.unstubAllGlobals());
describe("Search results", () => {
  it("shows an idle state for an empty search", async () => {
    const app = results({ at: "/search?parse=1" });
    expect(screen.getAllByText("Search your library from the top bar").at(-1)).toBeVisible();
    expect(screen.queryByText("Searching…")).toBeNull();
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.includes("/status"))).toBe(true),
    );
    expect(app.requests().some((request) => request.url.startsWith("/api/v1/search?"))).toBe(false);
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("exposes search options on demand", async () => {
    const user = userEvent.setup();
    results();
    await screen.findByRole("link", { name: "Desk bracket" });
    expect(screen.getByRole("combobox", { name: "Search mode" })).not.toBeVisible();
    await user.click(screen.getByText("Search options"));
    expect(screen.getByRole("combobox", { name: "Search mode" })).toBeVisible();
    await user.click(screen.getByText("Search options"));
    expect(screen.getByRole("combobox", { name: "Search mode" })).not.toBeVisible();
  });
  it("changes the result layout without repeating the search", async () => {
    const user = userEvent.setup();
    const app = results();
    await screen.findByRole("link", { name: "Desk bracket" });
    expect(screen.getByRole("button", { name: "Grid view" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "List view" }));
    expect(screen.getByRole("button", { name: "List view" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("link", { name: "Desk bracket" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Grid view" }));
    expect(screen.getByRole("button", { name: "Grid view" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(app.requests().filter(({ url }) => url.startsWith("/api/v1/search?"))).toHaveLength(1);
  });
  it("offers retry when the search deadline expires", async () => {
    const user = userEvent.setup();
    const app = results({
      routes: { "GET /api/v1/search?": json({ detail: "search_timeout" }, 408) },
    });
    expect(await screen.findByText("The search took too long. Try again.")).toBeVisible();
    app.route({ "GET /api/v1/search?": json(searchResponse({ items: [aSearchResult()] })) });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
  });
  it("shows model previews in text results", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:search-preview");
    results({
      routes: {
        "GET /api/v1/search?": json(
          searchResponse({
            items: [
              aSearchResult({
                model: aModelListItem({ thumbnail_url: "/api/v1/files/search-preview/thumbnail" }),
              }),
            ],
          }),
        ),
        "GET /api/v1/files/search-preview/thumbnail": new Response(
          new Blob(["preview"], { type: "image/png" }),
        ),
      },
    });
    expect(await screen.findByAltText("")).toHaveAttribute("src", "blob:search-preview");
    expect(screen.getByText("1 result shown")).toBeVisible();
  });
  it("preserves the selected search mode when changing sort", async () => {
    const user = userEvent.setup();
    const app = results({ at: "/search?q=bracket&mode=lexical&type=model" });
    await screen.findByRole("link", { name: "Desk bracket" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Sort results" }), "name-asc");
    await waitFor(() =>
      expect(
        app
          .requests()
          .some(
            ({ url }) =>
              url.includes("sort=name-asc") &&
              url.includes("mode=lexical") &&
              url.includes("types%5B%5D=model"),
          ),
      ).toBe(true),
    );
  });
  it("discards private image queries after an identity change", async () => {
    const user = userEvent.setup();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:private-query");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const app = renderApp(
      <AuthContext.Provider value={adminSession()}>
        <SearchPage />
      </AuthContext.Provider>,
      {
        at: "/search?image=1",
        routes: {
          "GET /api/v1/search/status": json(searchStatus({ legs: ["thumbnail"] })),
          "POST /api/v1/search/image?": json(searchResponse({ items: [aSearchResult()] })),
        },
      },
    );
    const input = screen.getByLabelText("Choose image", { selector: "input" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.upload(input, new File(["private image"], "query.png", { type: "image/png" }));
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
    app.rerender(
      <AuthContext.Provider value={memberSession()}>
        <SearchPage />
      </AuthContext.Provider>,
    );
    expect(screen.queryByRole("img")).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:private-query");
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("uses the Model query endpoint without sending a name or image", async () => {
    const app = results({
      at: "/search?model=17",
      routes: {
        "GET /api/v1/models/17/similar-text?": (_url, init) => {
          expect(init?.cache).toBe("no-store");
          return json(searchResponse({ items: [aSearchResult()] }));
        },
      },
    });
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
    expect(screen.getByText("Find related Models", { selector: "p" })).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.requests().some((request) => request.url.includes("/search?q="))).toBe(false);
  });
  it("clears an image query without retaining its data", async () => {
    const user = userEvent.setup();
    const preview = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:private-query");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const image = new File(["image bytes"], "private-location.png", { type: "image/png" });
    const app = results({
      at: "/search?image=1",
      routes: {
        "GET /api/v1/search/status": json(
          searchStatus({ enabled: true, semantic_ready: true, legs: ["lexical", "thumbnail"] }),
        ),
        "POST /api/v1/search/image?": (_url, init) => {
          expect(init?.body).toBe(image);
          expect(init?.cache).toBe("no-store");
          return json(
            searchResponse({
              items: [
                aSearchResult({
                  evidence: [{ leg: "thumbnail", field: "visual", text: "", ranges: [] }],
                }),
              ],
            }),
          );
        },
      },
    });
    const input = await screen.findByLabelText("Choose image", { selector: "input" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.upload(input, image);
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
    expect(screen.queryByText("Why this result")).toBeNull();
    expect(screen.getByRole("img", { name: "Image used for this search" })).toHaveAttribute(
      "src",
      "blob:private-query",
    );
    expect(app.requests().some((request) => request.url.includes("private-location"))).toBe(false);
    expect(preview).toHaveBeenCalledWith(image);
    await user.click(screen.getByRole("button", { name: "Clear image" }));
    expect(screen.queryByRole("link", { name: "Desk bracket" })).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:private-query");
  });
  it("requires a ready visual index before accepting images", async () => {
    const app = results({ at: "/search?image=1" });
    expect(await screen.findByText(/Visual search needs a ready visual index/)).toBeVisible();
    expect(screen.getByLabelText("Choose image", { selector: "input" })).toBeDisabled();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("rejects oversized image selection before sending it", async () => {
    const user = userEvent.setup();
    const app = results({
      at: "/search?image=1",
      routes: { "GET /api/v1/search/status": json(searchStatus({ legs: ["thumbnail"] })) },
    });
    const input = await screen.findByLabelText("Choose image", { selector: "input" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.upload(
      input,
      new File([new Uint8Array(8 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("no larger than 8 MB");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("omits retrieval explanations from result cards", async () => {
    const lexical = {
      leg: "lexical",
      field: "description",
      text: "Two bolts secure the bracket.",
      ranges: [],
    };
    results({
      routes: {
        "GET /api/v1/search?": json(
          searchResponse({
            items: [aSearchResult({ evidence: [lexical, { ...lexical, leg: "semantic_text" }] })],
          }),
        ),
      },
    });
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
    expect(screen.queryByText("Why this result")).toBeNull();
    expect(screen.queryByText("Two bolts secure the bracket.")).toBeNull();
  });
  it("restarts at the first page after a generation expires the cursor", async () => {
    const user = userEvent.setup();
    const app = results({
      routes: {
        "GET /api/v1/search?": (url) =>
          url.includes("cursor=")
            ? json({ detail: "search_cursor_expired" }, 409)
            : json(searchResponse({ items: [aSearchResult()], next_cursor: "old-generation" })),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Load more results" }));
    const restart = await screen.findByRole("button", { name: "Restart search" });
    app.route({
      "GET /api/v1/search?": json(
        searchResponse({ items: [aSearchResult({ name: "Current bracket" })] }),
      ),
    });
    await user.click(restart);
    expect(await screen.findByRole("link", { name: "Current bracket" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Desk bracket" })).toBeNull();
    expect(app.requests().filter((request) => request.url.includes("cursor=")).length).toBe(1);
  });
  it("links every authorized Subject type", async () => {
    const items = [
      aSearchResult(),
      aSearchResult({
        subject_type: "collection",
        subject_id: 3,
        name: "Tools",
        href: "/?c=Tools",
      }),
      aSearchResult({
        subject_type: "multipart_model",
        subject_id: 4,
        name: "Robot",
        href: "/multipart-models/4",
      }),
      aSearchResult({
        subject_type: "document",
        subject_id: 5,
        name: "Guide",
        href: "/documents/5",
        evidence: [
          {
            leg: "semantic_text",
            field: "text",
            text: "😀 <script>bracket</script>",
            ranges: [[10, 17]],
          },
        ],
      }),
    ];
    results({ routes: { "GET /api/v1/search?": json(searchResponse({ items })) } });
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toHaveAttribute(
      "href",
      "/models/12",
    );
    expect(screen.getByRole("link", { name: "Tools" })).toHaveAttribute("href", "/?c=Tools");
    expect(screen.getByRole("link", { name: "Robot" })).toHaveAttribute(
      "href",
      "/multipart-models/4",
    );
    expect(screen.getByRole("link", { name: "Guide" })).toHaveAttribute("href", "/documents/5");
    expect(screen.queryByText("Why this result")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });
  it("runs hybrid retrieval only for a submitted route", async () => {
    const app = results();
    await screen.findByRole("link", { name: "Desk bracket" });
    expect(app.requests().find((request) => request.url.includes("q=bracket"))?.url).toContain(
      "mode=hybrid",
    );
  });
  it.each<{ locale: "en" | "es"; message: string }>([
    { locale: "en", message: "Some search capabilities are unavailable." },
    { locale: "es", message: "Algunas funciones de búsqueda no están disponibles." },
  ])(
    "renders degraded search in $locale without losing available results",
    async ({ locale, message }) => {
      results({
        locale,
        routes: {
          "GET /api/v1/search?": json(
            searchResponse({ items: [aSearchResult()], degraded: ["search_semantic_unavailable"] }),
          ),
        },
      });
      expect(await screen.findByText(new RegExp(message))).toBeVisible();
      expect(screen.getByRole("link", { name: "Desk bracket" })).toBeVisible();
    },
  );
  it.each<{ locale: "en" | "es"; message: string }>([
    { locale: "en", message: "No strong matches" },
    { locale: "es", message: "No hay coincidencias claras" },
  ])("renders no strong matches honestly in $locale", async ({ locale, message }) => {
    results({
      locale,
      routes: { "GET /api/v1/search?": json(searchResponse({ outcome: "no_strong_matches" })) },
    });
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more results" })).toBeNull();
  });
  it.each<{ locale: "en" | "es"; message: string }>([
    { locale: "en", message: "The AI index is catching up." },
    { locale: "es", message: "El índice de IA se está actualizando." },
  ])("explains pending indexing in $locale", async ({ locale, message }) => {
    results({
      locale,
      routes: { "GET /api/v1/search/status": json(searchStatus({ backlog: true })) },
    });
    expect(await screen.findByText(new RegExp(message))).toHaveAttribute("role", "status");
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
  });
  it("loads the next opaque cursor without repeating the first page", async () => {
    const user = userEvent.setup();
    const app = results({
      routes: {
        "GET /api/v1/search?": (url) =>
          json(
            searchResponse(
              url.includes("cursor=next-page")
                ? {
                    items: [
                      aSearchResult({ subject_id: 14, name: "Second bracket", href: "/models/14" }),
                    ],
                  }
                : { items: [aSearchResult()], next_cursor: "next-page" },
            ),
          ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Load more results" }));
    expect(await screen.findByRole("link", { name: "Second bracket" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: "Desk bracket" })).toHaveLength(1);
    expect(app.requests().some((request) => request.url.includes("cursor=next-page"))).toBe(true);
  });
  it("applies Subject filters through the canonical URL", async () => {
    const user = userEvent.setup();
    const app = results();
    await user.click(screen.getByText("Search options"));
    await user.click(screen.getByRole("button", { name: "Document" }));
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.includes("types%5B%5D=document"))).toBe(
        true,
      ),
    );
  });
  it("offers recovery after a failed search", async () => {
    const user = userEvent.setup();
    const app = results({ routes: { "GET /api/v1/search?": json({}, 503) } });
    const retry = await screen.findByRole("button", { name: "Retry" });
    app.route({ "GET /api/v1/search?": json(searchResponse({ items: [aSearchResult()] })) });
    await user.click(retry);
    expect(await screen.findByRole("link", { name: "Desk bracket" })).toBeVisible();
  });
});
