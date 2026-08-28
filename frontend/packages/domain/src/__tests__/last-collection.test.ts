/*
 * Putting the user back where they were when they click "Vault".
 *
 * The remembered value is a collection *path*, and the href built from it goes
 * into the router — so encoding is the whole risk. A path with nesting and
 * spaces (`Parts/Cable Clips`) that is not encoded produces a URL that resolves
 * to something else or to nothing, and the user lands on an empty page having
 * asked to go back to their models.
 *
 * The root is stored as "no collection" rather than as an empty path, because an
 * empty path round-tripped through the href builder is the one value that would
 * silently mean "the root" in one place and "a collection named nothing" in
 * another.
 *
 * Everything here is best-effort by design. This is a convenience, not data: a
 * browser that refuses storage — private mode, a locked-down profile, a
 * non-browser render — must degrade to "start at the root", never throw. A
 * convenience that can crash the shell is worse than no convenience at all, which
 * is why the unavailable-storage cases are tested rather than assumed.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LAST_COLLECTION_STORAGE_KEY,
  LAST_VIEW_STORAGE_KEY,
  lastVaultHref,
  readLastCollection,
  readLastView,
  rememberLastCollection,
  rememberLastView,
} from "../last-collection";

/**
 * Replace `localStorage` with a property that throws on *access*, which is what a
 * browser configured to block site data does — the failure happens before any
 * method is called, so a try/catch around `getItem` alone would not see it.
 */
function withUnreachableStorage(body: () => void): void {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("access denied");
    },
  });
  try {
    body();
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
  }
}

/** A store that refuses every operation, as a locked-down browser profile does. */
const HOSTILE_STORAGE = {
  getItem: () => {
    throw new Error("storage disabled");
  },
  setItem: () => {
    throw new Error("storage disabled");
  },
  removeItem: () => {
    throw new Error("storage disabled");
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.removeItem(LAST_COLLECTION_STORAGE_KEY);
  window.localStorage.removeItem(LAST_VIEW_STORAGE_KEY);
});

describe("rememberLastCollection", () => {
  it("stores the path it was given", () => {
    rememberLastCollection("spoolers");

    expect(readLastCollection()).toBe("spoolers");
  });

  it("clears the remembered collection at the root", () => {
    rememberLastCollection("spoolers");

    rememberLastCollection(null);

    expect(readLastCollection()).toBeNull();
  });

  it("says nothing when the browser refuses to store it", () => {
    vi.stubGlobal("localStorage", HOSTILE_STORAGE);

    expect(() => rememberLastCollection("spoolers")).not.toThrow();
  });
});

describe("readLastCollection", () => {
  it("returns null when nothing is stored", () => {
    expect(readLastCollection()).toBeNull();
  });

  it("reads an empty stored path as nothing stored", () => {
    // The root is "no collection"; an empty string would mean "a collection
    // named nothing" one layer up.
    window.localStorage.setItem(LAST_COLLECTION_STORAGE_KEY, "");

    expect(readLastCollection()).toBeNull();
  });

  it("returns null when the browser refuses to read", () => {
    vi.stubGlobal("localStorage", HOSTILE_STORAGE);

    expect(readLastCollection()).toBeNull();
  });
});

describe("rememberLastView", () => {
  it("stores the tab the user was on", () => {
    rememberLastView("docs");

    expect(readLastView()).toBe("docs");
  });

  it("says nothing when the browser refuses to store it", () => {
    vi.stubGlobal("localStorage", HOSTILE_STORAGE);

    expect(() => rememberLastView("docs")).not.toThrow();
  });
});

describe("readLastView", () => {
  it("starts at the model grid", () => {
    expect(readLastView()).toBe("models");
  });

  it("reads anything it did not write as the model grid", () => {
    window.localStorage.setItem(LAST_VIEW_STORAGE_KEY, "unexpected");

    expect(readLastView()).toBe("models");
  });

  it("falls back to the model grid when the browser refuses to read", () => {
    vi.stubGlobal("localStorage", HOSTILE_STORAGE);

    expect(readLastView()).toBe("models");
  });
});

describe("lastVaultHref", () => {
  it("points at the root when nothing is remembered", () => {
    expect(lastVaultHref()).toBe("/");
  });

  it("carries the remembered collection", () => {
    rememberLastCollection("spoolers");

    expect(lastVaultHref()).toBe("/?c=spoolers");
  });

  it("encodes paths with nesting and spaces", () => {
    rememberLastCollection("spoolers/old prints");

    expect(lastVaultHref()).toBe("/?c=spoolers%2Fold%20prints");
  });

  it("carries the documents tab", () => {
    rememberLastView("docs");

    expect(lastVaultHref()).toBe("/?v=docs");
  });

  it("carries the collection and the tab together", () => {
    rememberLastCollection("spoolers");
    rememberLastView("docs");

    expect(lastVaultHref()).toBe("/?c=spoolers&v=docs");
  });

  it("leaves the model grid out of the href", () => {
    // The default needs no parameter, or every vault link carries a redundant
    // one and the two spellings of the same page cache separately.
    rememberLastCollection("spoolers");
    rememberLastView("models");

    expect(lastVaultHref()).toBe("/?c=spoolers");
  });

  it("points at the root when the browser refuses to read", () => {
    vi.stubGlobal("localStorage", HOSTILE_STORAGE);

    expect(lastVaultHref()).toBe("/");
  });

  it("points at the root when storage cannot even be reached", () => {
    withUnreachableStorage(() => {
      expect(lastVaultHref()).toBe("/");
    });
  });
});
