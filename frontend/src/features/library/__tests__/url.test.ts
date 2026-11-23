/**
 * Library navigation has two modes and URL-owned sort state. Canonical links
 * preserve unrelated filters while resolving legacy or untrusted input once.
 */
import { describe, expect, it } from "vitest";
import { LIBRARY_DIALOG_PARAMS, libraryDestinationHref, readLibraryLocation } from "../url";

describe("readLibraryLocation", () => {
  it.each(["organized", "components"])("normalizes retired URL mode %s", (view) => {
    const result = readLibraryLocation(
      new URLSearchParams(`type=${view}&c=parts&tag=tools&sort=name-asc`),
    );
    expect(result).toEqual({
      section: "models",
      view: "all",
      sort: "name-asc",
      href: "/?type=all&c=parts&tag=tools&sort=name-asc",
    });
  });

  it.each(["organized", "components"])("normalizes retired preference %s", (view) => {
    expect(readLibraryLocation(new URLSearchParams(), { view, sort: "name-asc" })).toEqual({
      section: "models",
      view: "all",
      sort: "name-asc",
      href: "/?type=all&sort=name-asc",
    });
  });

  it("gives an explicit mode priority over preferences", () => {
    expect(
      readLibraryLocation(new URLSearchParams("type=multipart"), { view: "all", sort: "date-desc" })
        .view,
    ).toBe("multipart");
  });

  it("initializes absent inputs from valid preferences", () => {
    expect(
      readLibraryLocation(new URLSearchParams("c=parts"), { view: "multipart", sort: "name-desc" }),
    ).toEqual({
      section: "models",
      view: "multipart",
      sort: "name-desc",
      href: "/?c=parts&type=multipart&sort=name-desc",
    });
  });

  it("rejects malformed explicit values instead of using preferences", () => {
    expect(
      readLibraryLocation(new URLSearchParams("type=invalid&sort=invalid"), {
        view: "multipart",
        sort: "name-asc",
      }),
    ).toEqual({
      section: "models",
      view: "all",
      sort: "date-desc",
      href: "/?type=all&sort=date-desc",
    });
  });

  it("preserves document navigation parameters", () => {
    expect(readLibraryLocation(new URLSearchParams("v=docs&c=guides&tag=assembly")).href).toBe(
      "/?v=docs&c=guides&tag=assembly&type=all&sort=date-desc",
    );
  });

  it("maps the legacy multipart bookmark", () => {
    expect(readLibraryLocation(new URLSearchParams("v=multipart&c=parts"))).toEqual({
      section: "models",
      view: "multipart",
      sort: "date-desc",
      href: "/?c=parts&type=multipart&sort=date-desc",
    });
  });

  it("gives the explicit mode priority over a legacy bookmark", () => {
    expect(readLibraryLocation(new URLSearchParams("v=multipart&type=all")).view).toBe("all");
  });

  it("is stable when its canonical URL is read again", () => {
    const first = readLibraryLocation(new URLSearchParams("c=parts&tag=tools&tag=home"), {
      view: "multipart",
      sort: "cost-asc",
    });
    expect(readLibraryLocation(new URLSearchParams(first.href.slice(2)))).toEqual(first);
  });
});

describe("libraryDestinationHref", () => {
  it.each(LIBRARY_DIALOG_PARAMS.map((key) => ({ key })))(
    "drops the $key dialog parameter from the displayed destination",
    ({ key }) => {
      expect(libraryDestinationHref(`/?type=all&c=parts&${key}=1&sort=name-asc`)).toBe(
        "/?type=all&c=parts&sort=name-asc",
      );
    },
  );

  it("keeps every filter that changes what the Library shows", () => {
    const href = "/?type=all&c=parts&cid=4&q=bracket&tag=a&tag=b&sort=name-asc";
    expect(libraryDestinationHref(href)).toBe(href);
  });
});
