import { uiText } from "@/lib/locale";
import { knownUiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
/* eslint-disable react-refresh/only-export-components */
import { useState } from "react";
import { Cloud, HardDrive, Network, Server } from "lucide-react";

import { Button } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { StorageProviderFields } from "@/components/storage-provider-fields";
import { providerDefaults } from "@/lib/storage-provider-form";
import { cn } from "@/lib/utils";
import { useOptionalI18n } from "@/lib/i18n";
import { storageOperationMessage } from "@/lib/storage-operations";
import type { ProviderCategory, StorageProvider, StorageProviderConfigValues } from "@/types";

const CATEGORIES: Array<{
  id: Exclude<ProviderCategory, "consumer_cloud">;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    id: "this_machine",
    get label() {
      return uiText("This machine");
    },
    icon: HardDrive,
  },
  {
    id: "s3_compatible",
    get label() {
      return uiText("S3-compatible object storage");
    },
    icon: Cloud,
  },
  {
    id: "nextcloud_webdav",
    get label() {
      return uiText("Nextcloud and WebDAV");
    },
    icon: Network,
  },
  {
    id: "nas_sftp",
    get label() {
      return uiText("NAS over SFTP");
    },
    icon: Server,
  },
];

const ONBOARDING_CATEGORIES = {
  this_machine: {
    get title() {
      return uiText("Local folder");
    },
    get description() {
      return uiText("On this server");
    },
  },
  s3_compatible: {
    get title() {
      return uiText("S3-compatible");
    },
    get description() {
      return uiText("Bucket on MinIO, Garage, R2 or AWS");
    },
  },
  nextcloud_webdav: {
    get title() {
      return uiText("WebDAV / Nextcloud");
    },
    get description() {
      return uiText("A folder on your Nextcloud");
    },
  },
  nas_sftp: {
    get title() {
      return uiText("SFTP");
    },
    get description() {
      return uiText("A folder on your NAS over SSH");
    },
  },
} as const;

export type ProviderValues = StorageProviderConfigValues;

export function defaultProviderValues(provider: StorageProvider): ProviderValues {
  return providerDefaults(provider);
}

/**
 * How a chosen provider is named back to the person who chose it. A local
 * provider is named by the choice they picked ("Local folder") rather than the
 * catalogue's provider label, so setup's review repeats what they clicked.
 */
export function storageChoiceLabel(provider: StorageProvider): string {
  return provider.category === "this_machine"
    ? ONBOARDING_CATEGORIES.this_machine.title
    : knownUiText(provider.label);
}

function providerConsequences(
  provider: StorageProvider,
  catalog: string,
  retention: string,
): string[] {
  if (provider.expected_tier !== "guarded") return provider.consequences;

  // Keep the deletion contract visible even if an older backend catalogue has
  // not populated its free-form consequences list yet.
  return [catalog, retention, ...provider.consequences].filter(
    (consequence, index, all) => all.indexOf(consequence) === index,
  );
}

/**
 * What choosing this storage means, in plain words. Tier names and support
 * levels are operator detail shown elsewhere in Settings; here the visitor
 * only needs the consequence for their files.
 */
export function StorageProviderSummary({ provider }: { provider: StorageProvider }) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const consequences = providerConsequences(
    provider,
    i18n?.t("storage.guardedCatalog") ?? uiText("storage.guardedCatalog"),
    i18n?.t("storage.guardedRetention") ?? uiText("storage.guardedRetention"),
  );
  const beta = provider.support_level === "beta";
  if (consequences.length === 0 && !beta) return null;
  return (
    <p className="text-xs leading-relaxed text-on-surface-variant">
      {[
        ...(beta ? [i18n?.t("storage.betaNote") ?? uiText("storage.betaNote")] : []),
        ...consequences.map((consequence) => knownUiText(consequence)),
      ].join(" ")}
    </p>
  );
}

export function StorageProviderPicker(props: {
  providers: StorageProvider[];
  providerId: string;
  values: ProviderValues;
  onProviderChange: (provider: StorageProvider) => void;
  onValueChange: (name: string, value: string | number) => void;
  disabled?: boolean;
  onboarding?: boolean;
  /** Fields the last storage check named as the problem. */
  invalidFields?: readonly string[];
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const selected = props.providers.find((provider) => provider.id === props.providerId);
  const [categoryOverride, setCategoryOverride] = useState<ProviderCategory | null>(null);
  const selectedCategory = categoryOverride ?? selected?.category ?? "this_machine";
  const categoryProviders = props.providers.filter(
    (provider) => provider.category === selectedCategory,
  );
  const secretFieldsSet = new Set(
    Array.isArray(props.values.secret_fields_set) ? props.values.secret_fields_set : [],
  );

  return (
    <div className="space-y-5">
      <fieldset className="space-y-2">
        <legend
          className={
            props.onboarding
              ? "sr-only"
              : "text-xs font-mono uppercase tracking-wider text-on-surface-variant"
          }
        >
          {props.onboarding ? uiText("setup.storageChoice") : uiText("Storage category")}
        </legend>
        <div className={props.onboarding ? "grid grid-cols-2 gap-2" : "grid gap-2 sm:grid-cols-2"}>
          {CATEGORIES.map((category) => {
            const Icon = category.icon;
            const hasProviders = props.providers.some(
              (provider) =>
                provider.category === category.id && (!props.onboarding || provider.selectable),
            );
            const active = selectedCategory === category.id;
            return (
              <Button
                key={category.id}
                type="button"
                variant="outline"
                disabled={props.disabled || !hasProviders}
                aria-pressed={active}
                aria-label={
                  props.onboarding ? ONBOARDING_CATEGORIES[category.id].title : category.label
                }
                onClick={() => {
                  const choices = props.providers.filter(
                    (provider) => provider.category === category.id,
                  );
                  const firstAvailable = choices.find((provider) => provider.selectable);
                  if (props.onboarding && firstAvailable) {
                    setCategoryOverride(null);
                    if (selected?.category !== category.id) props.onProviderChange(firstAvailable);
                  } else setCategoryOverride(category.id);
                }}
                className={cn(
                  "h-auto justify-start gap-2 whitespace-normal px-3 py-3 text-left",
                  props.onboarding && "min-h-16 items-start",
                  active &&
                    (props.onboarding
                      ? "border-primary bg-accent text-accent-foreground hover:bg-accent"
                      : "border-transparent bg-accent text-accent-foreground hover:bg-accent"),
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden />
                {props.onboarding ? (
                  <span className="min-w-0">
                    <span className="block text-xs font-medium">
                      {ONBOARDING_CATEGORIES[category.id].title}
                    </span>
                    <span className="mt-1 block text-xs font-normal text-muted-foreground">
                      {category.id === "this_machine" && props.values.data_dir
                        ? `${String(props.values.data_dir)} · `
                        : ""}
                      {ONBOARDING_CATEGORIES[category.id].description}
                    </span>
                  </span>
                ) : (
                  category.label
                )}
              </Button>
            );
          })}
        </div>
        {props.onboarding &&
          props.providers
            .filter(
              (provider) =>
                !provider.selectable && (provider.uses?.vault?.reason ?? provider.disabled_reason),
            )
            .map((provider) => (
              <p key={provider.id} className="text-xs text-muted-foreground">
                {knownUiText(provider.label)}:{" "}
                {storageOperationMessage(
                  provider.uses?.vault?.reason ?? provider.disabled_reason ?? "",
                  i18n?.t,
                )}
              </p>
            ))}
      </fieldset>

      {!(
        props.onboarding &&
        categoryProviders.length === 1 &&
        categoryProviders[0].id === selected?.id
      ) &&
        (props.onboarding ? (
          <div className="space-y-2">
            <label className="block space-y-2 text-xs font-mono uppercase tracking-wider text-on-surface-variant">
              {uiText("Provider")}
              <select
                className={cn(
                  inputClasses,
                  "bg-surface-container-lowest font-sans normal-case tracking-normal text-on-surface focus-visible:ring-ring",
                )}
                value={props.providerId}
                disabled={props.disabled}
                onChange={(event) => {
                  const provider = categoryProviders.find(
                    (candidate) => candidate.id === event.target.value,
                  );
                  if (provider?.selectable) {
                    setCategoryOverride(null);
                    props.onProviderChange(provider);
                  }
                }}
              >
                {categoryProviders.map((provider) => (
                  <option key={provider.id} value={provider.id} disabled={!provider.selectable}>
                    {knownUiText(provider.label)}
                  </option>
                ))}
              </select>
            </label>
            {categoryProviders
              .filter((provider) => !provider.selectable)
              .map((provider) => {
                const reason = provider.uses?.vault?.reason ?? provider.disabled_reason;
                return reason ? (
                  <p key={provider.id} className="text-xs text-muted-foreground">
                    {knownUiText(provider.label)}: {storageOperationMessage(reason, i18n?.t)}
                  </p>
                ) : null;
              })}
          </div>
        ) : (
          <fieldset className="space-y-2">
            <legend className="text-xs font-mono uppercase tracking-wider text-on-surface-variant">
              {uiText("Provider")}
            </legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {categoryProviders.map((provider) => (
                <Button
                  key={provider.id}
                  type="button"
                  variant="outline"
                  disabled={props.disabled || !provider.selectable}
                  aria-pressed={provider.id === props.providerId}
                  title={
                    provider.uses?.vault && !provider.uses.vault.available
                      ? storageOperationMessage(provider.uses.vault.reason, i18n?.t)
                      : provider.disabled_reason
                        ? storageOperationMessage(provider.disabled_reason, i18n?.t)
                        : undefined
                  }
                  onClick={() => {
                    setCategoryOverride(null);
                    props.onProviderChange(provider);
                  }}
                  className={cn(
                    "h-auto min-h-16 justify-start whitespace-normal px-3 py-3 text-left",
                    provider.id === props.providerId &&
                      "border-transparent bg-accent text-accent-foreground hover:bg-accent",
                  )}
                >
                  <span>
                    <span className="block text-sm font-medium">{knownUiText(provider.label)}</span>
                    <span className="block text-xs font-normal opacity-70">
                      {provider.uses?.vault && !provider.uses.vault.available
                        ? storageOperationMessage(provider.uses.vault.reason, i18n?.t)
                        : provider.disabled_reason
                          ? storageOperationMessage(provider.disabled_reason, i18n?.t)
                          : knownUiText(provider.description)}
                    </span>
                  </span>
                </Button>
              ))}
            </div>
          </fieldset>
        ))}

      {selected && (!props.onboarding || selected.category === selectedCategory) && (
        <section
          className={
            props.onboarding && selected.id === "local"
              ? "space-y-3"
              : "space-y-4 border-t border-border pt-4"
          }
        >
          {props.onboarding && selected.id === "local" ? (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {uiText("setup.serverPaths")}
            </p>
          ) : (
            <StorageProviderSummary provider={selected} />
          )}

          <StorageProviderFields
            provider={selected}
            values={props.values}
            onChange={props.onValueChange}
            disabled={props.disabled}
            storedSecrets={[...secretFieldsSet]}
            omitFields={props.onboarding && selected.id === "local" ? ["root"] : undefined}
            invalidFields={props.invalidFields}
          />
        </section>
      )}
    </div>
  );
}
