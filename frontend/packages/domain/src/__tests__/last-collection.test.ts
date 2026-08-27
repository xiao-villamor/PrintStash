import { describe, expect, it } from "vitest";

import {
  LAST_VIEW_STORAGE_KEY,
  lastVaultHref,
  readLastCollection,
  readLastView,
  rememberLastCollection,
  rememberLastView,
} from "../last-collection";

describe("last vault context", () => {
  it("remembers, encodes, and clears collection paths", () => {
    expect(readLastCollection()).toBeNull();
    expect(lastVaultHref()).toBe("/");

    rememberLastCollection("spoolers/old prints");
    expect(readLastCollection()).toBe("spoolers/old prints");
    expect(lastVaultHref()).toBe("/?c=spoolers%2Fold%20prints");

    rememberLastCollection(null);
    expect(lastVaultHref()).toBe("/");
  });

  it("remembers the documents tab and combines it with the collection", () => {
    expect(readLastView()).toBe("models");
    rememberLastCollection("spoolers");
    rememberLastView("docs");

    expect(readLastView()).toBe("docs");
    expect(lastVaultHref()).toBe("/?c=spoolers&v=docs");

    window.localStorage.setItem(LAST_VIEW_STORAGE_KEY, "unexpected");
    expect(readLastView()).toBe("models");
  });
});
