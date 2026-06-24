import { afterEach, describe, expect, it } from "vitest";

import {
  LAST_COLLECTION_STORAGE_KEY,
  lastVaultHref,
  readLastCollection,
  rememberLastCollection,
} from "@/lib/last-collection";

afterEach(() => {
  window.localStorage.removeItem(LAST_COLLECTION_STORAGE_KEY);
});

describe("last collection persistence", () => {
  it("returns null and the root href when nothing is stored", () => {
    expect(readLastCollection()).toBeNull();
    expect(lastVaultHref()).toBe("/");
  });

  it("remembers a collection path and builds a restoring href", () => {
    rememberLastCollection("spoolers");
    expect(readLastCollection()).toBe("spoolers");
    expect(lastVaultHref()).toBe("/?c=spoolers");
  });

  it("encodes paths with nesting and spaces", () => {
    rememberLastCollection("spoolers/old prints");
    expect(lastVaultHref()).toBe("/?c=spoolers%2Fold%20prints");
  });

  it("clears the remembered collection at the root", () => {
    rememberLastCollection("spoolers");
    rememberLastCollection(null);
    expect(readLastCollection()).toBeNull();
    expect(lastVaultHref()).toBe("/");
  });
});
