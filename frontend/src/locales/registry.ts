/**
 * The locales the running app knows, without their messages.
 *
 * `catalogs.ts` is the authoring registry: it imports every catalog statically,
 * which is what the build plugin, the scaffolding script and the catalog checks
 * need. The app must not load every language to show one, so at runtime it
 * reads this file instead: display metadata that is always present, and a
 * loader per catalog that fetches it only when that locale is shown.
 */
import type { localeDefinitions, MessageCatalog } from "./catalogs";

export type Locale = keyof typeof localeDefinitions;

/** Native display name and writing direction; kept equal to `catalogs.ts` by a repository check. */
export const localeMetadata = {
  en: { name: "English", direction: "ltr" },
  es: { name: "Español", direction: "ltr" },
} satisfies Record<Locale, { name: string; direction: "ltr" | "rtl" }>;

const loaders = import.meta.glob<MessageCatalog>("./*.json", { import: "default" });

/** Fetch one locale's catalog. A registered locale without a catalog file is a build error. */
export function loadLocaleCatalog(locale: Locale): Promise<MessageCatalog> {
  const load = loaders[`./${locale}.json`];
  if (!load) throw new Error(`locale_catalog_missing: ${locale}`);
  return load();
}
