/** History metadata is session-owned and only settled views can become return targets. */
import { act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { renderApp } from "@/test-support/render";
import {
  acknowledgeFavoriteRemoval,
  readLibraryPosition,
  saveLibraryPosition,
  knownOrigin,
  useLibraryEntry,
  type LibraryEntry,
  type LibraryReadingPosition,
} from "../navigation-state";

function Probe({
  ready = true,
  href = "/?tag=Private&type=all&sort=date-desc",
  capture,
}: {
  ready?: boolean;
  href?: string;
  capture: (entry: LibraryEntry) => void;
}) {
  const entry = useLibraryEntry(href, ready);
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

describe("confirmed favorite reading metadata", () => {
  it.each([
    {
      label: "favorite anchor",
      href: "/?favorites=true",
      removed: "/models/1",
      expected: "/models/2",
    },
    { label: "Everything anchor", href: "/?type=all", removed: "/models/1", expected: "/models/1" },
    {
      label: "unaffected favorite",
      href: "/?favorites=true",
      removed: "/models/9",
      expected: "/models/1",
    },
  ])(
    "promotes a neighboring anchor only for the affected $label",
    ({ href, removed, expected }) => {
      let entry!: LibraryEntry;
      renderApp(
        <Probe
          href={href}
          capture={(value) => {
            entry = value;
          }}
        />,
      );
      const position: LibraryReadingPosition = {
        main: 0,
        list: 900,
        anchor: { key: "/models/1", offset: 50, container: "list" },
        adjacent: { key: "/models/2", offset: 100, container: "list" },
        pages: { models: 2, folders: 1 },
      };
      saveLibraryPosition(entry, "list", position);

      acknowledgeFavoriteRemoval(removed, getSessionVersion());

      const saved = readLibraryPosition(entry, "list");
      expect(saved?.anchor?.key).toBe(expected);
      expect(saved?.pages).toEqual(position.pages);
      expect(saved?.anchor?.offset).toBe(expected === "/models/2" ? 100 : 50);
    },
  );

  it("retains explicit reset when the removed favorite has no neighbor", () => {
    let entry!: LibraryEntry;
    renderApp(
      <Probe
        href="/?favorites=true"
        capture={(value) => {
          entry = value;
        }}
      />,
    );
    const position: LibraryReadingPosition = {
      main: 0,
      list: 50,
      anchor: { key: "/models/1", offset: 50, container: "list" },
      adjacent: null,
      pages: { models: 1, folders: 1 },
    };
    saveLibraryPosition(entry, "list", position);

    acknowledgeFavoriteRemoval("/models/1", getSessionVersion());

    expect(readLibraryPosition(entry, "list")).toEqual(position);
  });

  it("rejects a retired favorite receipt for reading metadata", () => {
    const retired = getSessionVersion();
    act(() => clearLogin());
    let entry!: LibraryEntry;
    renderApp(
      <Probe
        href="/?favorites=true"
        capture={(value) => {
          entry = value;
        }}
      />,
    );
    const position: LibraryReadingPosition = {
      main: 0,
      list: 900,
      anchor: { key: "/models/1", offset: 50, container: "list" },
      adjacent: { key: "/models/2", offset: 100, container: "list" },
      pages: { models: 2, folders: 1 },
    };
    saveLibraryPosition(entry, "list", position);

    acknowledgeFavoriteRemoval("/models/1", retired);

    expect(readLibraryPosition(entry, "list")).toEqual(position);
  });
});
