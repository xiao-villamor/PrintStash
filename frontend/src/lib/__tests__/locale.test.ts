/** Catalog interpolation handles CLDR plurals without interpreting user values. */
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
