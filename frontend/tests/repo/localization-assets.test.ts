/** Installation metadata and offline copy share the application's locale registry. */
import { describe, expect, it } from "vitest";
import { createLocalizationAssets } from "../../scripts/localization-assets";
import { SUPPORTED_LOCALES, localeDefinitions } from "@/lib/locale";

describe("localization assets", () => {
  it.each(SUPPORTED_LOCALES)("provides installation metadata in %s", (locale) => {
    const assets = createLocalizationAssets();
    const manifest = JSON.parse(assets.get(`/manifest.${locale}.webmanifest`) ?? "null");
    expect(manifest).toMatchObject({
      name: "PrintStash",
      lang: locale,
      dir: localeDefinitions[locale].direction,
      description: localeDefinitions[locale].messages["shell.description"],
    });
  });
});
