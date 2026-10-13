/*
 * Preview quality and screenshot scale, read back from the user's browser.
 *
 * The defaults are deliberate — balanced previews and 2x screenshots — because
 * both extremes have a cost a user would notice: the highest quality makes the
 * vault sluggish on a laptop, and the lowest makes the thumbnails useless.
 *
 * An unsupported value is replaced rather than passed through. These feed a
 * renderer, and an out-of-range quality is not a slightly-wrong preview; it is a
 * blank one.
 */

import { act, cleanup, renderHook } from "@testing-library/react";
import { usePreviewPreferences } from "@/lib/preview-preferences";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_PREVIEW_PREFERENCES,
  PREVIEW_PREFERENCES_STORAGE_KEY,
  previewPixelRatio,
  readPreviewPreferences,
  writePreviewPreferences,
  type PreviewPreferences,
} from "@/lib/preview-preferences";

describe("readPreviewPreferences", () => {
  it("uses balanced previews and 2x screenshots by default", () => {
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);
    expect(previewPixelRatio("balanced")).toBe(1.5);
  });

  it.each(["webgl", "auto", "webgpu"] as const)(
    "retains the %s mesh renderer choice",
    (meshRenderer) => {
      writePreviewPreferences({ ...DEFAULT_PREVIEW_PREFERENCES, meshRenderer });

      expect(readPreviewPreferences().meshRenderer).toBe(meshRenderer);
    },
  );

  it("keeps legacy preferences on the compatibility renderer", () => {
    localStorage.setItem(
      PREVIEW_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        previewQuality: "detail",
        screenshotScale: 3,
      }),
    );

    expect(readPreviewPreferences()).toEqual({
      previewQuality: "detail",
      screenshotScale: 3,
      meshRenderer: "webgl",
    });
  });

  it("refuses an unsupported mesh renderer preference", () => {
    localStorage.setItem(
      PREVIEW_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        ...DEFAULT_PREVIEW_PREFERENCES,
        meshRenderer: "cuda",
      }),
    );

    expect(readPreviewPreferences().meshRenderer).toBe("webgl");
  });

  it("round-trips supported quality settings", () => {
    writePreviewPreferences({
      previewQuality: "detail",
      meshRenderer: "webgl",
      screenshotScale: 3,
    });
    expect(readPreviewPreferences()).toEqual({
      previewQuality: "detail",
      meshRenderer: "webgl",
      screenshotScale: 3,
    });
  });

  it("replaces malformed or unsupported values with defaults", () => {
    localStorage.setItem(
      PREVIEW_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ previewQuality: "ultra", screenshotScale: 8 }),
    );
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);

    localStorage.setItem(PREVIEW_PREFERENCES_STORAGE_KEY, "broken");
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  writePreviewPreferences(DEFAULT_PREVIEW_PREFERENCES);
});

describe("optional preview preferences persistence", () => {
  it.each([{ failure: "property" }, { failure: "getItem" }])(
    "uses defaults when preview preferences storage $failure is blocked",
    ({ failure }) => {
      const error = new DOMException("blocked storage", "SecurityError");
      const spy =
        failure === "property"
          ? vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);
      } finally {
        spy.mockRestore();
      }
    },
  );
  it.each([{ failure: "property" }, { failure: "getItem" }])(
    "retains read preview preferences when storage $failure is blocked",
    ({ failure }) => {
      const choice: PreviewPreferences = {
        previewQuality: "detail",
        meshRenderer: "webgl",
        screenshotScale: 3,
      };
      localStorage.setItem(PREVIEW_PREFERENCES_STORAGE_KEY, JSON.stringify(choice));
      expect(readPreviewPreferences()).toEqual(choice);
      const error = new DOMException("blocked storage", "SecurityError");
      const spy =
        failure === "property"
          ? vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(readPreviewPreferences()).toEqual(choice);
      } finally {
        spy.mockRestore();
      }
    },
  );
  it.each([{ failure: "property" }, { failure: "setItem" }])(
    "retains selected preview preferences after storage $failure fails",
    ({ failure }) => {
      const choice: PreviewPreferences = {
        previewQuality: "detail",
        meshRenderer: "webgl",
        screenshotScale: 3,
      };
      writePreviewPreferences(DEFAULT_PREVIEW_PREFERENCES);
      const error = new DOMException("storage quota", "QuotaExceededError");
      const spy =
        failure === "property"
          ? vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(() => writePreviewPreferences(choice)).not.toThrow();
      } finally {
        spy.mockRestore();
      }
      expect(readPreviewPreferences()).toEqual(choice);
      expect(readPreviewPreferences()).toEqual(choice);
      expect(JSON.parse(localStorage.getItem(PREVIEW_PREFERENCES_STORAGE_KEY)!)).toEqual(
        DEFAULT_PREVIEW_PREFERENCES,
      );
    },
  );
  it("snapshots unpersisted preview preferences choices", () => {
    const choice: PreviewPreferences = {
      previewQuality: "detail",
      meshRenderer: "webgl",
      screenshotScale: 3,
    };
    const expected = structuredClone(choice);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      writePreviewPreferences(choice);
    } finally {
      spy.mockRestore();
    }
    choice.previewQuality = "performance";
    const result = readPreviewPreferences();
    result.screenshotScale = 1;
    expect(readPreviewPreferences()).toEqual(expected);
  });
  it("preserves preview preferences serialization errors", () => {
    const error = new TypeError("invalid preference encoding");
    const choice: PreviewPreferences = {
      previewQuality: "detail",
      meshRenderer: "webgl",
      screenshotScale: 3,
    };
    vi.spyOn(JSON, "stringify").mockImplementationOnce(() => {
      throw error;
    });
    expect(() => writePreviewPreferences(choice)).toThrow(error);
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);
  });
  it("releases pending preview preferences after persistence recovers", () => {
    const choice: PreviewPreferences = {
      previewQuality: "detail",
      meshRenderer: "webgl",
      screenshotScale: 3,
    };
    const external: PreviewPreferences = {
      previewQuality: "performance",
      meshRenderer: "webgl",
      screenshotScale: 1,
    };
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      writePreviewPreferences(choice);
    } finally {
      spy.mockRestore();
    }
    writePreviewPreferences(DEFAULT_PREVIEW_PREFERENCES);
    localStorage.setItem(PREVIEW_PREFERENCES_STORAGE_KEY, JSON.stringify(external));
    expect(readPreviewPreferences()).toEqual(external);
  });
});

describe("preview preference consumers", () => {
  it("delivers unpersisted preview choices to mounted consumers", () => {
    const view = renderHook(usePreviewPreferences);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      act(() =>
        writePreviewPreferences({
          previewQuality: "detail",
          meshRenderer: "webgl",
          screenshotScale: 3,
        }),
      );
      expect(view.result.current).toEqual({
        previewQuality: "detail",
        meshRenderer: "webgl",
        screenshotScale: 3,
      });
    } finally {
      spy.mockRestore();
      view.unmount();
    }
  });
});
