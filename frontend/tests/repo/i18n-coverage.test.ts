/** Guard authored JSX and accessibility copy at the source, before rendering. */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parseSync, Visitor, type Expression } from "oxc-parser";
import { CHANGELOG } from "@/lib/changelog";
import { describe, expect, it } from "vitest";
import { getMessageCatalog, isMessageKey, SUPPORTED_LOCALES } from "@/lib/locale";

// Brands, protocols, sample addresses and standard units have no translated copy.
const TECHNICAL = new Set([
  "SFTP",
  "PLA",
  "PrintStash",
  "PrintStash ·",
  "GCODE",
  "GCode",
  "G-code",
  "3D",
  "Authentik",
  "Klipper",
  "Moonraker",
  "URL",
  "°C",
  "Voron 2.4",
  "JSON",
  "GitHub",
  "Spoolman",
  "https://www.printables.com/model/...",
  "https://auth.example.com/application/o/printstash",
  "http://mk4.local",
  "http://octopi.local",
  "http://printer.local:7125",
  "/api/v1/auth/login",
  "http://spoolman.local:7912",
  "https://example.com/hook",
  "https://discord.com/api/webhooks/…",
  "123456:ABC-DEF…",
  "-1001234567890",
  "https://ntfy.sh",
  "my-printer-alerts",
  "tk_…",
]);
const ATTRIBUTES = new Set([
  "title",
  "placeholder",
  "aria-label",
  "ariaLabel",
  "description",
  "label",
  "confirmLabel",
  "hint",
  "help",
  "emptyText",
  "cancelLabel",
  "closeLabel",
]);

function unlocalizedCopy(source: string): string[] {
  const values: string[] = [];
  const add = (value: string) => {
    const text = value.replace(/\s+/g, " ").trim();
    if (/[A-Za-z]/.test(text) && !TECHNICAL.has(text)) values.push(text);
  };
  const expression = (node: Expression) => {
    if (node.type === "ParenthesizedExpression") expression(node.expression);
    if (
      node.type === "Literal" &&
      "value" in node &&
      (node.raw?.startsWith('"') || node.raw?.startsWith("'"))
    )
      add(String(node.value));
    if (node.type === "ConditionalExpression") {
      expression(node.consequent);
      expression(node.alternate);
    }
    if (node.type === "LogicalExpression") expression(node.right);
    if (node.type === "TemplateLiteral")
      node.quasis.forEach((part) => add(part.value.cooked ?? part.value.raw));
  };
  const parsed = parseSync("copy.tsx", source, { lang: "tsx" });
  new Visitor({
    JSXText(node) {
      add(String(node.value));
    },
    JSXElement(node) {
      for (const child of node.children)
        if (
          child.type === "JSXExpressionContainer" &&
          child.expression.type !== "JSXEmptyExpression"
        )
          expression(child.expression);
    },
    JSXFragment(node) {
      for (const child of node.children)
        if (
          child.type === "JSXExpressionContainer" &&
          child.expression.type !== "JSXEmptyExpression"
        )
          expression(child.expression);
    },
    JSXAttribute(node) {
      if (node.name.type !== "JSXIdentifier" || !ATTRIBUTES.has(node.name.name)) return;
      if (node.value?.type === "Literal") add(node.value.value);
      if (
        node.value?.type === "JSXExpressionContainer" &&
        node.value.expression.type !== "JSXEmptyExpression"
      )
        expression(node.value.expression);
    },
    CallExpression(node) {
      if (node.callee.type !== "MemberExpression" || node.callee.property.type !== "Identifier")
        return;
      if (!new Set(["success", "warning", "info", "error"]).has(node.callee.property.name)) return;
      const object = node.callee.object;
      const isToast =
        (object.type === "Identifier" && object.name === "toast") ||
        (object.type === "MemberExpression" &&
          object.property.type === "Identifier" &&
          object.property.name === "toast");
      if (!isToast) return;
      const first = node.arguments[0];
      if (first?.type !== "SpreadElement") expression(first);
    },
    Property(node) {
      const key = node.key;
      const name =
        key.type === "Identifier" ? key.name : key.type === "Literal" ? String(key.value) : null;
      if (!node.computed && name !== null && ATTRIBUTES.has(name)) {
        if (node.value.type === "Literal") {
          if (node.value.value === null) return;
          const value = String(node.value.value);
          const semanticKey = /^[a-z][\w]*(?:\.[\w]+)+$/.test(value) && isMessageKey(value);
          if (!semanticKey) add(value);
        }
        if (node.value.type === "TemplateLiteral")
          node.value.quasis.forEach((part) => add(part.value.cooked ?? part.value.raw));
        if (node.value.type === "ConditionalExpression") expression(node.value);
      }
    },
  }).visit(parsed.program);
  return values;
}
function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory())
      return new Set(["__tests__", "generated", "test-support"]).has(entry.name)
        ? []
        : sourceFiles(path);
    return /\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts") ? [path] : [];
  });
}
function parameters(value: string): string[] {
  return [...new Set([...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]))].sort();
}

describe("translationCoverage", () => {
  it("rejects untranslated UI copy", () => {
    expect(
      unlocalizedCopy(
        '<button title="Open dialog" aria-label={`Delete ${name}`}>{ok ? "Done" : "Retry"} Save</button>',
      ),
    ).toEqual(expect.arrayContaining(["Open dialog", "Delete", "Done", "Retry", "Save"]));
    expect(
      unlocalizedCopy(
        '<button title={uiText("Open dialog")}>{uiText("Save")}{model.name}</button>',
      ),
    ).toEqual([]);
  });
  it("rejects hardcoded imperative presentation copy", () => {
    expect(
      unlocalizedCopy(
        'toast.success("Saved"); deps.toast.warning(`Skipped ${count}`); const field = { placeholder: "Helpful copy" };',
      ),
    ).toEqual(expect.arrayContaining(["Saved", "Skipped", "Helpful copy"]));
    expect(
      unlocalizedCopy(
        'toast.success(uiText("save.success")); const field = { placeholder: uiText("form.help") };',
      ),
    ).toEqual([]);
  });
  it("has no unwrapped authored JSX copy", () => {
    expect(
      sourceFiles("src").flatMap((file) =>
        unlocalizedCopy(readFileSync(file, "utf8")).map((text) => `${file}: ${text}`),
      ),
    ).toEqual([]);
  });
  it("has no hardcoded imperative presentation copy", () => {
    expect(
      sourceFiles("src").flatMap((file) =>
        unlocalizedCopy(readFileSync(file, "utf8")).map((text) => `${file}: ${text}`),
      ),
    ).toEqual([]);
  });
  it("keeps catalog text out of ignored component fallbacks", () => {
    expect(
      sourceFiles("src").flatMap((file) =>
        /\b_fallback\b/.test(readFileSync(file, "utf8")) ? [file] : [],
      ),
    ).toEqual([]);
  });
  it("localizes the release notes displayed in About", () => {
    const release = CHANGELOG[0];
    expect([release.date, ...release.changes].filter((message) => !isMessageKey(message))).toEqual(
      [],
    );
  });
  it.each(SUPPORTED_LOCALES)("validates every key, plural form and parameter in %s", (locale) => {
    const base = getMessageCatalog("en");
    const catalog = getMessageCatalog(locale);
    expect(Object.keys(catalog).sort()).toEqual(Object.keys(base).sort());
    for (const key of Object.keys(base).filter(isMessageKey)) {
      const source = base[key];
      const target = catalog[key];
      const sourceForms = source instanceof Object ? Object.values(source) : [source];
      const expected = [...new Set(sourceForms.flatMap(parameters))].sort();
      expect({ key, plural: target instanceof Object }).toEqual({
        key,
        plural: source instanceof Object,
      });
      const forms = target instanceof Object ? Object.values(target) : [target];
      expect({ key, fallback: target instanceof Object ? Boolean(target.other) : true }).toEqual({
        key,
        fallback: true,
      });
      for (const form of forms) {
        expect({ locale, key, empty: form.trim().length === 0 }).toEqual({
          locale,
          key,
          empty: false,
        });
        expect({ locale, key, parameters: parameters(form) }).toEqual({
          locale,
          key,
          parameters: expected,
        });
        expect({ locale, key, encodedEntity: /&(?:amp|quot|apos|lt|gt);/i.test(form) }).toEqual({
          locale,
          key,
          encodedEntity: false,
        });
        expect({ locale, key, draft: /TODO\([^)]+\):/.test(form) }).toEqual({
          locale,
          key,
          draft: false,
        });
      }
    }
  });
});
