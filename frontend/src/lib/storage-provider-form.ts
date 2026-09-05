import type {
  StorageConnectionConfiguration,
  StorageProvider,
  StorageProviderConfigValues,
} from "@/types";

export function providerFields(provider: StorageProvider, use = "vault") {
  return provider.fields_by_use?.[use] ?? provider.fields;
}

export function providerDefaults(provider: StorageProvider, use = "vault") {
  return Object.fromEntries(
    providerFields(provider, use)
      .filter((field) => field.default !== null && field.default !== undefined)
      .map((field) => [field.name, field.default ?? ""]),
  );
}

export function providerFormError(
  provider: StorageProvider,
  values: StorageProviderConfigValues,
  use = "vault",
  stored: string[] = [],
): string | null {
  const present = (name: string) =>
    String(values[name] ?? "").trim() !== "" ||
    (values[name] === undefined && stored.includes(name));
  const missing = providerFields(provider, use).find(
    (field) => field.required && !present(field.name),
  );
  if (missing) return `${missing.label} is required.`;
  for (const rule of provider.requirements ?? []) {
    const [first, second] = rule.fields;
    if (rule.kind === "exactly_one" && rule.fields.filter(present).length !== 1)
      return rule.message;
    if (rule.kind === "requires" && present(first) && !present(second)) return rule.message;
    if (rule.kind === "not_value" && String(values[first] ?? "") === rule.value)
      return rule.message;
  }
  return null;
}

export function splitProviderValues(
  provider: StorageProvider,
  values: StorageProviderConfigValues,
  use: string,
) {
  const configuration: StorageConnectionConfiguration = { provider: provider.id };
  const secrets: Record<string, string> = {};
  for (const field of providerFields(provider, use)) {
    const value = values[field.name];
    if (value === undefined || Array.isArray(value)) continue;
    if (field.secret) secrets[field.name] = String(value);
    else configuration[field.name] = field.input_type === "number" ? Number(value) : String(value);
  }
  return { configuration, secrets };
}
