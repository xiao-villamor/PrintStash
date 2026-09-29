/*
 * The filtered outliner shows only the folders on the way to what matched, named
 * from the labels the server sends with each item. If a label were paired with
 * the wrong path segment, a folder would appear under another folder's name; if
 * a shared ancestor were built twice, one folder would show up as two.
 */

import { describe, expect, it } from "vitest";

import { aCollectionNode } from "@/test-support/factories";
import { ancestorPaths, buildFilteredTree, labelledChain } from "@/lib/collection-tree";

describe("ancestorPaths", () => {
  it("lists the paths above a folder from the root down", () => {
    expect(ancestorPaths("parts/brackets/small")).toEqual(["parts", "parts/brackets"]);
  });

  it("returns nothing for a top-level folder", () => {
    expect(ancestorPaths("parts")).toEqual([]);
  });
});

describe("labelledChain", () => {
  it("pairs every name with its path", () => {
    expect(labelledChain("parts/wall-brackets", "Parts/Wall Brackets")).toEqual([
      { path: "parts", name: "Parts" },
      { path: "parts/wall-brackets", name: "Wall Brackets" },
    ]);
  });

  it("names the last folders when ancestors above them are hidden", () => {
    expect(labelledChain("parts/brackets/small", "Brackets/Small")).toEqual([
      { path: "parts/brackets", name: "Brackets" },
      { path: "parts/brackets/small", name: "Small" },
    ]);
  });
});

describe("buildFilteredTree", () => {
  it("builds a shared ancestor once", () => {
    const tree = buildFilteredTree([
      { path: "parts/brackets", label: "Parts/Brackets" },
      { path: "parts/gears", label: "Parts/Gears" },
    ]);

    expect(tree.map((folder) => folder.children.map((child) => child.name))).toEqual([
      ["Brackets", "Gears"],
    ]);
  });

  it("sorts siblings by name", () => {
    const tree = buildFilteredTree([
      { path: "toys", label: "Toys" },
      { path: "anchors", label: "Anchors" },
    ]);

    expect(tree.map((folder) => folder.name)).toEqual(["Anchors", "Toys"]);
  });

  it("keeps the collection an entry names", () => {
    const node = aCollectionNode({ id: 9, path: "parts", name: "Parts" });

    const tree = buildFilteredTree([{ path: "parts", label: "Parts", node }]);

    expect(tree[0].node?.id).toBe(9);
  });

  it("leaves a folder reached only as an ancestor without a collection", () => {
    const tree = buildFilteredTree([{ path: "parts/brackets", label: "Parts/Brackets" }]);

    expect(tree[0].node).toBeNull();
  });

  it("starts a hidden ancestor's subtree at the first visible folder", () => {
    const tree = buildFilteredTree([{ path: "parts/brackets/small", label: "Brackets/Small" }]);

    expect(tree.map((folder) => folder.path)).toEqual(["parts/brackets"]);
  });
});
