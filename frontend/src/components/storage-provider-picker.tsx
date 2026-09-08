import { uiText } from "@/lib/locale";
import { knownUiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
/* eslint-disable react-refresh/only-export-components */
import { useState } from "react";
import { Cloud, HardDrive, Network, Server } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

export type ProviderValues = StorageProviderConfigValues;

export function defaultProviderValues(provider: StorageProvider): ProviderValues {
  return providerDefaults(provider);
}

function tierLabel(tier: string): string {
  return knownUiText(tier);
}

function supportLabel(level: string | undefined): string {
  return knownUiText(level ?? "stable");
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

export function StorageProviderPicker(props: {
  providers: StorageProvider[];
  providerId: string;
  values: ProviderValues;
  onProviderChange: (provider: StorageProvider) => void;
  onValueChange: (name: string, value: string | number) => void;
  disabled?: boolean;
  activeTier?: string;
  onboarding?: boolean;
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
  const consequences = selected
    ? providerConsequences(
        selected,
        i18n?.t("storage.guardedCatalog") ?? uiText("storage.guardedCatalog"),
        i18n?.t("storage.guardedRetention") ?? uiText("storage.guardedRetention"),
      )
    : [];

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
        <div className={props.onboarding ? "grid grid-cols-3 gap-2" : "grid gap-2 sm:grid-cols-2"}>
          {CATEGORIES.map((category) => {
            const Icon = category.icon;
            const hasProviders = props.providers.some(
              (provider) => provider.category === category.id,
            );
            const active = selectedCategory === category.id;
            return (
              <Button
                key={category.id}
                type="button"
                variant={props.onboarding && category.id !== "this_machine" ? "ghost" : "outline"}
                disabled={props.disabled || !hasProviders}
                aria-pressed={active}
                aria-label={
                  props.onboarding && category.id === "this_machine"
                    ? uiText("setup.serverStorage")
                    : category.label
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
                  props.onboarding &&
                    (category.id === "this_machine"
                      ? "col-span-3 min-h-12"
                      : "min-h-11 flex-col justify-center px-1 py-2 text-center text-xs sm:flex-row"),
                  active && "border-transparent bg-accent text-accent-foreground hover:bg-accent",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden />
                {props.onboarding ? uiText(`setup.category.${category.id}`) : category.label}
                {props.onboarding && category.id === "this_machine" && (
                  <span className="ml-auto text-xs font-normal">{uiText("setup.recommended")}</span>
                )}
              </Button>
            );
          })}
        </div>
      </fieldset>

      {!(
        props.onboarding &&
        categoryProviders.length === 1 &&
        categoryProviders[0].id === selected?.id
      ) && (
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
      )}

      {selected && (!props.onboarding || selected.category === selectedCategory) && (
        <section
          className={
            props.onboarding && selected.id === "local"
              ? "space-y-3"
              : "space-y-4 rounded-lg border border-outline-variant bg-surface-container-low p-4"
          }
        >
          {props.onboarding && selected.id === "local" ? (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {uiText("setup.serverPaths")}
            </p>
          ) : (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">
                  {i18n?.t("settings.storageSupport", {
                    level: supportLabel(selected.support_level),
                  }) ??
                    uiText("Support: {value1}", {
                      value1: String(supportLabel(selected.support_level)),
                    })}
                </Badge>
                <Badge variant="secondary">
                  {uiText("Expected: {value1}", {
                    value1: String(tierLabel(selected.expected_tier) ?? ""),
                  })}
                </Badge>
                {props.activeTier && (
                  <Badge variant="outline">
                    {uiText("Active: {value1}", {
                      value1: String(tierLabel(props.activeTier) ?? ""),
                    })}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-on-surface">{knownUiText(selected.expected_tier_note)}</p>
              {selected.expected_tier === "guarded" && (
                <p className="text-xs font-medium text-on-surface">
                  {uiText("Guarded storage consequences")}
                </p>
              )}
              {consequences.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-xs text-on-surface-variant">
                  {consequences.map((consequence) => (
                    <li key={consequence}>{knownUiText(consequence)}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <StorageProviderFields
            provider={selected}
            values={props.values}
            onChange={props.onValueChange}
            disabled={props.disabled}
            storedSecrets={[...secretFieldsSet]}
            omitFields={props.onboarding && selected.id === "local" ? ["root"] : undefined}
          />
        </section>
      )}
    </div>
  );
}
