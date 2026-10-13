import type { Message, MessageCatalog, MessageKey } from "@/locales/catalogs";
import { loadLocaleCatalog, localeMetadata, type Locale } from "@/locales/registry";

/** Display metadata of every locale; messages are loaded per locale on demand. */
export const localeDefinitions = localeMetadata;
export type { Locale } from "@/locales/registry";
export type { Message, MessageCatalog, MessageKey, PluralMessage } from "@/locales/catalogs";
export type MessageValues = Record<string, string | number>;
export interface MessageDescriptor {
  key: MessageKey;
  values?: MessageValues;
}
export function uiMessage(key: MessageKey, values?: MessageValues): MessageDescriptor {
  return { key, values };
}

// SAFETY: Locale is exactly the own keys of this literal registry.
export const SUPPORTED_LOCALES = Object.keys(localeDefinitions).filter((key): key is Locale =>
  Object.hasOwn(localeDefinitions, key),
);
// The locale last put on screen. A newly chosen locale replaces it only once
// its catalog has arrived, so the interface never shows a language it cannot
// yet speak.
let shownLocale: Locale | null = null;
const catalogs = new Map<Locale, MessageCatalog>();
const loading = new Map<Locale, Promise<void>>();
let catalogLoader: (locale: Locale) => Promise<MessageCatalog> = loadLocaleCatalog;

/** Point catalog loading at a different source (tests); what was loaded is forgotten. */
export function setLocaleCatalogLoader(
  loader: (locale: Locale) => Promise<MessageCatalog> = loadLocaleCatalog,
): void {
  catalogLoader = loader;
  catalogs.clear();
  loading.clear();
  shownLocale = null;
}

/**
 * Make one locale's messages available. Concurrent callers share the request;
 * a failed request is forgotten so the next attempt fetches again.
 */
export function ensureLocaleCatalog(locale: Locale): Promise<void> {
  if (catalogs.has(locale)) return Promise.resolve();
  const pending = loading.get(locale);
  if (pending) return pending;
  const request = catalogLoader(locale)
    .then((catalog) => {
      catalogs.set(locale, catalog);
    })
    .finally(() => loading.delete(locale));
  loading.set(locale, request);
  return request;
}

/** The messages of a locale that is already loaded; asking for another is a programming error. */
export function getMessageCatalog(locale: Locale): MessageCatalog {
  const catalog = catalogs.get(locale);
  if (!catalog) throw new Error(`locale_catalog_not_loaded: ${locale}`);
  return catalog;
}
export const LOCALE_STORAGE_KEY = "printstash.locale";
const LOCALE_EVENT = "printstash:locale";

export function parseLocale(value: string | null): Locale | null {
  return SUPPORTED_LOCALES.find((locale) => locale === value) ?? null;
}

export function getLocale(): Locale {
  try {
    return parseLocale(globalThis.localStorage?.getItem(LOCALE_STORAGE_KEY) ?? null) ?? "en";
  } catch {
    return "en";
  }
}

let transientLocale: Locale | null = null;

/** The locale the user asked for, whether or not its catalog is loaded yet. */
export function requestedLocale(): Locale {
  return transientLocale ?? getLocale();
}

export function currentLocale(): Locale {
  const requested = requestedLocale();
  if (catalogs.has(requested)) shownLocale = requested;
  // Before the requested catalog arrives, show whichever locale can be shown.
  shownLocale ??= catalogs.keys().next().value ?? null;
  return shownLocale ?? requested;
}

/** Announce a locale once it can be shown; a catalog that cannot be fetched leaves the current one. */
function showWhenLoaded(locale: Locale): void {
  const announce = () =>
    globalThis.dispatchEvent?.(new CustomEvent(LOCALE_EVENT, { detail: locale }));
  // A catalog already here switches at once; only a first use waits for its fetch.
  if (catalogs.has(locale)) {
    announce();
    return;
  }
  ensureLocaleCatalog(locale).then(announce, (error: Error) =>
    console.warn("locale catalog failed to load:", error),
  );
}

/**
 * Load the catalog the app starts in. A requested locale whose catalog cannot
 * be fetched starts in English instead of leaving the app without any text.
 */
export async function activateLocale(): Promise<Locale> {
  const requested = requestedLocale();
  try {
    await ensureLocaleCatalog(requested);
  } catch (error) {
    if (requested === "en") throw error;
    await ensureLocaleCatalog("en");
    shownLocale = "en";
  }
  return currentLocale();
}

export function setLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    transientLocale = null;
  } catch {
    transientLocale = locale;
  }
  showWhenLoaded(locale);
}

export function subscribeLocale(listener: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    // Another tab chose a locale; this tab may not have its catalog yet.
    if (event.key === LOCALE_STORAGE_KEY || event.key === null) showWhenLoaded(requestedLocale());
  };
  globalThis.addEventListener?.(LOCALE_EVENT, listener);
  globalThis.addEventListener?.("storage", onStorage);
  return () => {
    globalThis.removeEventListener?.(LOCALE_EVENT, listener);
    globalThis.removeEventListener?.("storage", onStorage);
  };
}

/** Every catalog has the same keys, so the one on screen answers for all of them. */
export function isMessageKey(value: string): value is MessageKey {
  return Object.hasOwn(getMessageCatalog(currentLocale()), value);
}

/** Parameters are substituted after plural selection, never translated as UI. */
export function formatMessage(
  locale: string,
  message: Message,
  values: MessageValues = {},
): string {
  const text =
    message instanceof Object
      ? (message[new Intl.PluralRules(locale).select(Number(values.count ?? 0))] ?? message.other)
      : message;
  return text.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    Object.hasOwn(values, name) ? String(values[name]) : placeholder,
  );
}

export function translate(locale: Locale, key: MessageKey, values?: MessageValues): string {
  return formatMessage(locale, getMessageCatalog(locale)[key], values);
}

/** Explicit source-message lookup; never apply this to user-authored content. */
export function uiText(key: MessageKey, values?: MessageValues): string {
  return translate(currentLocale(), key, values);
}

/** Presentation boundary for server-owned enums and provider field descriptions. */
export function knownUiText(value: string, locale = currentLocale()): string {
  return isMessageKey(value) ? translate(locale, value) : value;
}

export function translateUiText(locale: Locale, value: string): string {
  return knownUiText(value, locale);
}
