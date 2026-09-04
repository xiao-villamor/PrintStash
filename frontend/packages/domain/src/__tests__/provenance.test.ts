/*
 * How a captured record describes where it came from, kept language-neutral.
 *
 * The domain package deliberately renders nothing. `provenanceOriginKey` returns
 * the origin unchanged precisely so a UI can look it up in its own typed catalog —
 * the moment this layer returns "Confirmed" instead of `confirmed`, the string is
 * English forever and every consumer has to un-translate it to branch on it.
 *
 * The completion states are the opposite case and the reason they sit next to each
 * other: three values, one of which is `null`, and `null` means *in progress*
 * rather than unknown. An import still running that reads as "Partial" tells the
 * user it finished and dropped things.
 */

import { describe, expect, it } from "vitest";

import { formatInboxCompletion, provenanceOriginKey, type ProvenanceOrigin } from "../provenance";

describe("provenanceOriginKey", () => {
  it.each(["confirmed", "inferred", "user"] satisfies ProvenanceOrigin[])(
    "passes %s through without choosing a display language",
    (origin) => {
      expect(provenanceOriginKey(origin)).toBe(origin);
    },
  );
});

describe("formatInboxCompletion", () => {
  it("reports a finished import as complete", () => {
    expect(formatInboxCompletion("complete")).toBe("Complete");
  });

  it("reports an import that dropped things as partial", () => {
    expect(formatInboxCompletion("partial")).toBe("Partial");
  });

  it("reports an unfinished import as still in progress", () => {
    // `null` is "not done yet", not "unknown" — reading it as partial tells the
    // user an import that is still running has already lost something.
    expect(formatInboxCompletion(null)).toBe("In progress");
  });
});
