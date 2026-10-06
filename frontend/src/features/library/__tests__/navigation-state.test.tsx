/** History metadata is session-owned and only settled views can become return targets. */
import { act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { renderApp } from "@/test-support/render";
import { knownOrigin, useLibraryEntry, type LibraryEntry } from "../navigation-state";

function Probe({
  ready = true,
  capture,
}: {
  ready?: boolean;
  capture: (entry: LibraryEntry) => void;
}) {
  const entry = useLibraryEntry("/?tag=Private&type=all&sort=date-desc", ready);
  capture(entry);
  return <p>Library</p>;
}

describe("Library entry ownership", () => {
  it("registers only a settled displayed identity", () => {
    let entry!: LibraryEntry;
    const view = renderApp(
      <Probe
        ready={false}
        capture={(value) => {
          entry = value;
        }}
      />,
    );
    expect(knownOrigin({ libraryOrigin: entry.key })).toBeUndefined();
    view.rerender(
      <Probe
        capture={(value) => {
          entry = value;
        }}
      />,
    );
    expect(knownOrigin({ libraryOrigin: entry.key })).toEqual(entry);
  });

  it("does not re-register a retired view after its session changes", () => {
    let entry!: LibraryEntry;
    renderApp(
      <Probe
        capture={(value) => {
          entry = value;
        }}
      />,
    );
    const identity = { libraryOrigin: entry.key };
    expect(knownOrigin(identity)).toEqual(entry);

    act(() => clearLogin());

    expect(knownOrigin(identity)).toBeUndefined();
  });

  it.each([null, undefined, {}, { libraryOrigin: 1 }, { libraryOrigin: "never-registered" }])(
    "rejects an unknown history origin %j",
    (state) => {
      expect(knownOrigin(state)).toBeUndefined();
    },
  );
});
