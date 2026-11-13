/** Existing deep links keep their route identity, public boundary and repeated search parameters. */
import { afterAll, describe, expect, it } from "vitest";
import { router } from "../router";
import { parseAppSearch } from "@/lib/router-search";

afterAll(() => router.history.destroy());

describe("router", () => {
  it.each([
    { path: "/", route: "/_app/" },
    { path: "/search", route: "/_app/search" },
    { path: "/models/42", route: "/_app/models/$id" },
    { path: "/library/similar", route: "/_app/library/similar" },
    { path: "/library/similar/42", route: "/_app/library/similar_/$id" },
    { path: "/builds", route: "/_app/builds" },
    { path: "/builds/42", route: "/_app/builds_/$id" },
    { path: "/multipart-models/42", route: "/_app/multipart-models/$id" },
    { path: "/documents/42", route: "/_app/documents/$id" },
    { path: "/inbox", route: "/_app/inbox" },
    { path: "/inbox/42", route: "/_app/inbox_/$id" },
    { path: "/printers", route: "/_app/printers" },
    { path: "/printers/42", route: "/_app/printers_/$id" },
    { path: "/profiles", route: "/_app/profiles" },
    { path: "/statistics", route: "/_app/statistics" },
    { path: "/settings", route: "/_app/settings" },
    { path: "/login", route: "/_app/login" },
    { path: "/setup", route: "/_app/setup" },
    { path: "/getting-started", route: "/_app/getting-started" },
    { path: "/not-a-route", route: "/_app/$" },
  ])("resolves the existing $path deep link", ({ path, route }) => {
    const matches = router.matchRoutes(path);

    expect(matches.at(-1)?.routeId).toBe(route);
    expect(matches.map((match) => match.routeId)).toContain("/_app");
  });

  it("parses detail identity from a direct build URL", () => {
    const matches = router.matchRoutes("/builds/42");

    expect(matches.at(-1)?.params).toMatchObject({ id: "42" });
  });

  it("keeps a shared viewer outside the private boundary", () => {
    const matches = router.matchRoutes("/share/public-token");

    expect(matches.map((match) => match.routeId)).toEqual(["__root__", "/share/$token"]);
    expect(matches.at(-1)?.params).toMatchObject({ token: "public-token" });
  });

  it("preserves the repeated Library search wire format", () => {
    const href = "/?c=parts&q=gear&tag=Desk&tag=Tools&v=7&type=all&sort=name-asc&favorites=true";
    const location = router.buildLocation({
      to: "/",
      search: parseAppSearch(new URL(href, "https://printstash.example").search),
    });

    expect(new URLSearchParams(location.searchStr).getAll("tag")).toEqual(["Desk", "Tools"]);
    expect(location.href).toBe(href);
  });
});
