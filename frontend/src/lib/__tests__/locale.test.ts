/**
 * Catalog interpolation handles CLDR plurals without interpreting user values,
 * and a locale's messages are fetched only when that locale is shown.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatMessage, getLocale, knownUiText, parseLocale, setLocale, uiText } from "../locale";

afterEach(() => {
  vi.restoreAllMocks();
  setLocale("en");
});

describe("formatMessage", () => {
  it.each([
    { locale: "en", count: 1, expected: "one" },
    { locale: "es", count: 2, expected: "other" },
    { locale: "ar", count: 0, expected: "zero" },
    { locale: "ar", count: 1, expected: "one" },
    { locale: "ar", count: 2, expected: "two" },
    { locale: "ar", count: 3, expected: "few" },
    { locale: "ar", count: 11, expected: "many" },
    { locale: "ar", count: 100, expected: "other" },
  ])("selects $locale plural for $count", ({ locale, count, expected }) => {
    const result = formatMessage(
      locale,
      { zero: "zero", one: "one", two: "two", few: "few", many: "many", other: "other" },
      { count },
    );

    expect(result).toBe(expected);
  });

  it("uses the other form when a category has no explicit translation", () => {
    expect(formatMessage("ar", { other: "{count} pieces" }, { count: 3 })).toBe("3 pieces");
  });

  it("preserves parameter text containing placeholders", () => {
    const name = "Files {count} $& <b>";

    const result = formatMessage("es", "{name}: {count}", { name, count: 2 });

    expect(result).toBe("Files {count} $& <b>: 2");
  });
});

describe("uiText", () => {
  it("uses the selected catalog for a plural message", () => {
    setLocale("es");

    expect(uiText("counts.files", { count: 1 })).toBe("1 archivo");
    expect(uiText("counts.files", { count: 2 })).toBe("2 archivos");
  });
});

describe("parseLocale", () => {
  it("rejects unsupported stored values", () => {
    expect(parseLocale("not-a-locale")).toBeNull();
  });
});

describe("getLocale", () => {
  it("defaults an unsupported stored value to English", () => {
    localStorage.setItem("printstash.locale", "not-a-locale");

    expect(getLocale()).toBe("en");
  });

  it("defaults to English when locale persistence cannot be read", () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });

    expect(getLocale()).toBe("en");
  });
});

describe("knownUiText", () => {
  it("retains an unrecognized external label verbatim", () => {
    expect(knownUiText("Custom provider label", "es")).toBe("Custom provider label");
  });
});

/** A fresh module graph: nothing loaded, as in a tab that has not rendered yet. */
describe("catalog loading", () => {
  type LocaleModule = typeof import("../locale");
  let locale: LocaleModule;
  let catalogOf: LocaleModule["getMessageCatalog"];
  const loads: string[] = [];

  async function fresh(failing: readonly string[] = []): Promise<void> {
    vi.resetModules();
    loads.length = 0;
    const loaded = await import("../locale");
    const registry = await import("@/locales/registry");
    loaded.setLocaleCatalogLoader((requested) => {
      loads.push(requested);
      return failing.includes(requested)
        ? Promise.reject(new Error("offline"))
        : registry.loadLocaleCatalog(requested);
    });
    locale = loaded;
    catalogOf = loaded.getMessageCatalog;
  }

  afterEach(() => {
    localStorage.clear();
  });

  it("refuses the messages of a locale that was never loaded", async () => {
    await fresh();

    expect(() => catalogOf("es")).toThrow("locale_catalog_not_loaded: es");
  });

  it("starts with only the requested locale's catalog", async () => {
    await fresh();
    localStorage.setItem("printstash.locale", "es");

    await locale.activateLocale();

    expect(loads).toEqual(["es"]);
  });

  it("starts in English when nothing was chosen", async () => {
    await fresh();

    expect(await locale.activateLocale()).toBe("en");
  });

  it("starts in English when the requested catalog cannot be fetched", async () => {
    await fresh(["es"]);
    localStorage.setItem("printstash.locale", "es");

    expect(await locale.activateLocale()).toBe("en");
  });

  it("keeps the requested locale stored when it could not be fetched", async () => {
    await fresh(["es"]);
    localStorage.setItem("printstash.locale", "es");

    await locale.activateLocale();

    expect(localStorage.getItem("printstash.locale")).toBe("es");
  });

  it("fails to start when even English cannot be fetched", async () => {
    await fresh(["en"]);

    await expect(locale.activateLocale()).rejects.toThrow("offline");
  });

  it("shares one request between concurrent callers", async () => {
    await fresh();

    await Promise.all([locale.ensureLocaleCatalog("es"), locale.ensureLocaleCatalog("es")]);

    expect(loads).toEqual(["es"]);
  });

  it("fetches again after a failed request", async () => {
    await fresh(["es"]);
    await locale.ensureLocaleCatalog("es").catch(() => {});

    await locale.ensureLocaleCatalog("es").catch(() => {});

    expect(loads).toEqual(["es", "es"]);
  });

  it("keeps showing the current locale until the chosen one has loaded", async () => {
    await fresh();
    await locale.activateLocale();

    locale.setLocale("es");

    expect(locale.currentLocale()).toBe("en");
  });

  it("announces a chosen locale once its catalog has loaded", async () => {
    await fresh();
    await locale.activateLocale();
    const announced = vi.fn<() => void>();
    const stop = locale.subscribeLocale(announced);

    locale.setLocale("es");
    await vi.waitFor(() => expect(announced).toHaveBeenCalled());
    stop();

    expect(locale.uiText("counts.files", { count: 2 })).toBe("2 archivos");
  });

  it("switches at once to a locale that is already loaded", async () => {
    await fresh();
    await locale.activateLocale();
    await locale.ensureLocaleCatalog("es");

    locale.setLocale("es");

    expect(locale.currentLocale()).toBe("es");
  });

  it("stays on the current locale when the chosen catalog cannot be fetched", async () => {
    await fresh(["es"]);
    await locale.activateLocale();
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});

    locale.setLocale("es");
    await vi.waitFor(() => expect(warned).toHaveBeenCalled());

    expect(locale.currentLocale()).toBe("en");
  });

  it("loads the locale another tab chose before showing it", async () => {
    await fresh();
    await locale.activateLocale();
    const announced = vi.fn<() => void>();
    const stop = locale.subscribeLocale(announced);

    localStorage.setItem("printstash.locale", "es");
    window.dispatchEvent(new StorageEvent("storage", { key: "printstash.locale" }));
    await vi.waitFor(() => expect(announced).toHaveBeenCalled());
    stop();

    expect(locale.currentLocale()).toBe("es");
  });

  it("recognizes message keys from the catalog on screen", async () => {
    await fresh();
    localStorage.setItem("printstash.locale", "es");
    await locale.activateLocale();

    expect(locale.isMessageKey("counts.files")).toBe(true);
  });
});
