import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";
import { localeDefinitions, type Message } from "../src/locales/catalogs.ts";

type Catalogs = Record<string, Record<string, Message>>;

/** One shared key table; null means the translation is identical to its key. */
export function compactCatalogs(catalogs: Catalogs) {
  const keys = Object.keys(catalogs.en);
  const values = Object.fromEntries(
    Object.entries(catalogs).map(([locale, messages]) => {
      if (
        Object.keys(messages).length !== keys.length ||
        keys.some((key) => !Object.hasOwn(messages, key))
      ) {
        throw new Error(`Locale ${locale} must contain exactly the English message keys.`);
      }
      return [locale, keys.map((key) => (messages[key] === key ? null : messages[key]))];
    }),
  );
  return { keys, values };
}

/** Compact only production JSON modules; source catalogs remain the authoring API. */
export function compactLocaleBundles(): Plugin {
  const localesDir = resolve(dirname(fileURLToPath(import.meta.url)), "../src/locales");
  const catalogs = Object.fromEntries(
    Object.entries(localeDefinitions).map(([locale, definition]) => [locale, definition.messages]),
  );
  const { keys, values } = compactCatalogs(catalogs);
  const keyId = "\0printstash:locale-keys";
  const prefix = "\0printstash:locale:";
  const modules = new Map(
    Object.keys(catalogs).map((locale) => [resolve(localesDir, `${locale}.json`), locale]),
  );

  return {
    name: "printstash-compact-locales",
    apply: "build",
    enforce: "pre",
    async resolveId(source, importer) {
      if (source === keyId) return keyId;
      if (!source.endsWith(".json")) return;
      // Let Vite resolve aliases as well as relative imports. Both must share
      // the same compact module or the original English catalog is duplicated.
      const resolved = await this.resolve(source, importer, { skipSelf: true });
      const locale = modules.get(resolved?.id ?? "");
      if (locale) return `${prefix}${locale}`;
    },
    load(id) {
      if (id === keyId) return `export default ${JSON.stringify(keys)};`;
      if (!id.startsWith(prefix)) return;
      const locale = id.slice(prefix.length);
      return `import keys from ${JSON.stringify(keyId)};
        const values = ${JSON.stringify(values[locale])};
        export default Object.fromEntries(keys.map((key, i) => [key, values[i] ?? key]));`;
    },
  };
}
