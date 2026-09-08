import en from "./en.json" with { type: "json" };
import es from "./es.json" with { type: "json" };

export type MessageKey = keyof typeof en;
export type PluralMessage = { other: string } & Partial<Record<Intl.LDMLPluralRule, string>>;
export type Message = string | PluralMessage;
export type MessageCatalog = Record<MessageKey, Message>;

/** Register a complete catalog and its native display name here. */
export const localeDefinitions = {
  en: { name: "English", direction: "ltr", messages: en },
  es: { name: "Español", direction: "ltr", messages: es },
} satisfies Record<string, { name: string; direction: "ltr" | "rtl"; messages: MessageCatalog }>;

export type Locale = keyof typeof localeDefinitions;
