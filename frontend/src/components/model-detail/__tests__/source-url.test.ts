/*
 * The render-boundary defense for links PrintStash captured from somebody else's
 * website.
 *
 * A provenance record's creator, licence, and canonical URLs arrive from a remote
 * page, so they are attacker-controlled by construction. The moment one reaches an
 * `href` unchecked, `javascript:` and `data:` become script execution inside the
 * app, on a page that already holds the user's session. This function is the only
 * thing standing between the two, which is why the rejections are enumerated here
 * one case per test rather than swept in a loop: when this goes red, which shape
 * got through is the entire question.
 *
 * Credentials in the authority (`https://user:secret@…`) are rejected for a
 * different reason: they render as a plausible hostname while pointing somewhere
 * else, which is a phishing link the user cannot see through.
 */

import { describe, expect, it } from "vitest";

import { safeHttpUrl } from "../source-url";

/** Written as a code point so the fixture survives every editor and diff tool. */
const NUL = String.fromCharCode(0);

describe("safeHttpUrl", () => {
  it("accepts an https link", () => {
    expect(safeHttpUrl("https://example.test/creator")).toBe("https://example.test/creator");
  });

  it("accepts a plain http link", () => {
    expect(safeHttpUrl("http://example.test/license")).toBe("http://example.test/license");
  });

  it("normalizes what it accepts", () => {
    // The returned value is what reaches the `href`, so it is the parsed URL rather
    // than the captured string — the two differ on anything a browser would resolve.
    expect(safeHttpUrl("https://Example.test")).toBe("https://example.test/");
  });

  it.each([
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,boom"],
    ["a file: URL", "file:///etc/passwd"],
    ["credentials hiding the real host", "https://user:secret@example.test/"],
    ["a control character smuggled into the path", `https://example.test/${NUL}trick`],
    ["something that is not a URL at all", "not a url"],
    ["an empty string", ""],
  ])("rejects %s", (_case, value) => {
    expect(safeHttpUrl(value)).toBeNull();
  });
});
