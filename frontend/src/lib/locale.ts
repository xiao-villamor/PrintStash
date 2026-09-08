import en from "@/locales/en.json";
import {
  localeDefinitions,
  type Locale,
  type Message,
  type MessageCatalog,
  type MessageKey,
} from "@/locales/catalogs";
export { localeDefinitions } from "@/locales/catalogs";
export type {
  Locale,
  Message,
  MessageCatalog,
  MessageKey,
  PluralMessage,
} from "@/locales/catalogs";
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
export function getMessageCatalog(locale: Locale): MessageCatalog {
  return localeDefinitions[locale].messages;
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

export function currentLocale(): Locale {
  return transientLocale ?? getLocale();
}

export function setLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    transientLocale = null;
  } catch {
    transientLocale = locale;
  }
  globalThis.dispatchEvent?.(new CustomEvent(LOCALE_EVENT, { detail: locale }));
}

export function subscribeLocale(listener: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === LOCALE_STORAGE_KEY || event.key === null) listener();
  };
  globalThis.addEventListener?.(LOCALE_EVENT, listener);
  globalThis.addEventListener?.("storage", onStorage);
  return () => {
    globalThis.removeEventListener?.(LOCALE_EVENT, listener);
    globalThis.removeEventListener?.("storage", onStorage);
  };
}

export function isMessageKey(value: string): value is MessageKey {
  return Object.hasOwn(en, value);
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
  return formatMessage(locale, getMessageCatalog(locale)[key] ?? en[key], values);
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
