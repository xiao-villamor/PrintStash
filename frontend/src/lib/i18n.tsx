/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";

import {
  currentLocale,
  localeDefinitions,
  setLocale,
  subscribeLocale,
  translate,
  type Locale,
  type MessageKey,
  type MessageValues,
} from "./locale";

export { getMessageCatalog, SUPPORTED_LOCALES, localeDefinitions } from "./locale";
export type { Locale, MessageKey, MessageCatalog } from "./locale";

type I18nValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, values?: MessageValues) => string;
};
const I18nContext = createContext<I18nValue | null>(null);

/** Subscribe surfaces using imperative display helpers, including portals. */
export function useUiLocale(): Locale {
  return useSyncExternalStore(subscribeLocale, currentLocale, () => "en");
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const locale = useUiLocale();
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = localeDefinitions[locale].direction;
    try {
      localStorage.setItem("printstash.locale", locale);
    } catch {
      // The active selection still works when storage is unavailable.
    }
  }, [locale]);
  const value = useMemo<I18nValue>(
    () => ({ locale, setLocale, t: (key, values) => translate(locale, key, values) }),
    [locale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within I18nProvider");
  return value;
}

export function useOptionalI18n(): I18nValue | null {
  return useContext(I18nContext);
}
