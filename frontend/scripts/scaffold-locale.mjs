import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

function canonicalLocale(value) {
  try {
    const locale = Intl.getCanonicalLocales(value)[0];
    if (locale && /^[A-Za-z0-9-]+$/.test(locale)) return locale;
  } catch {
    // Intl owns BCP 47 parsing; callers receive one stable authoring error.
  }
  throw new Error(`Invalid locale tag: ${value}`);
}

function draftMessage(value, locale) {
  if (value instanceof Object)
    return Object.fromEntries(
      Object.entries(value).map(([form, message]) => [form, draftMessage(message, locale)]),
    );
  return `TODO(${locale}): ${String(value)}`;
}

function importName(locale) {
  return `catalog${locale
    .split("-")
    .map((part) => `${part[0].toUpperCase()}${part.slice(1).toLowerCase()}`)
    .join("")}`;
}

/**
 * Create a complete, bundled locale draft and register its static JSON import.
 * Draft markers deliberately fail the catalog guard until every value is reviewed.
 */
export function scaffoldLocale({ rootDir, locale: rawLocale, name, direction }) {
  if (direction !== "ltr" && direction !== "rtl")
    throw new Error('Direction must be either "ltr" or "rtl".');
  if (!name?.trim()) throw new Error("A native language name is required.");

  const locale = canonicalLocale(rawLocale);
  const localesDir = join(rootDir, "src/locales");
  const sourcePath = join(localesDir, "en.json");
  const catalogPath = join(localesDir, `${locale}.json`);
  const registryPath = join(localesDir, "catalogs.ts");
  if (existsSync(catalogPath)) throw new Error(`Locale ${locale} already exists.`);

  const registry = readFileSync(registryPath, "utf8");
  const specifier = `./${locale}.json`;
  if (registry.includes(specifier)) throw new Error(`Locale ${locale} already exists.`);

  const source = JSON.parse(readFileSync(sourcePath, "utf8"));
  const draft = Object.fromEntries(
    Object.entries(source).map(([key, message]) => [key, draftMessage(message, locale)]),
  );
  const identifier = importName(locale);
  const importLine = `import ${identifier} from ${JSON.stringify(specifier)} with { type: "json" };\n`;
  const lastImport = [...registry.matchAll(/^import .*;\n/gm)].at(-1);
  if (!lastImport) throw new Error("Could not find the static locale imports in catalogs.ts.");
  const importEnd = lastImport.index + lastImport[0].length;
  const withImport = `${registry.slice(0, importEnd)}${importLine}${registry.slice(importEnd)}`;
  const key = /^[A-Za-z_$][\w$]*$/.test(locale) ? locale : JSON.stringify(locale);
  const definition = `  ${key}: { name: ${JSON.stringify(name.trim())}, direction: ${JSON.stringify(direction)}, messages: ${identifier} },\n`;
  if (!withImport.includes("} satisfies Record"))
    throw new Error("Could not find localeDefinitions in catalogs.ts.");
  const nextRegistry = withImport.replace("} satisfies Record", `${definition}} satisfies Record`);

  writeFileSync(catalogPath, `${JSON.stringify(draft, null, 2)}\n`);
  writeFileSync(registryPath, nextRegistry);
  return { locale, catalogPath, registryPath };
}

const invokedPath = process.argv[1];
if (invokedPath && fileURLToPath(import.meta.url) === invokedPath) {
  const args = process.argv.slice(2);
  if (args[0] === "--") args.shift();
  const [locale, name, direction = "ltr"] = args;
  if (!locale || !name) {
    console.error('Usage: pnpm i18n:add -- <BCP-47 tag> "<native name>" [ltr|rtl]');
    process.exitCode = 1;
  } else {
    try {
      const result = scaffoldLocale({
        rootDir: dirname(dirname(invokedPath)),
        locale,
        name,
        direction,
      });
      console.log(`Created ${result.catalogPath}`);
      console.log(`Translate every TODO(${result.locale}) marker, then run pnpm i18n:check.`);
    } catch (error) {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    }
  }
}
