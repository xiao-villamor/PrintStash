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
  id: ProviderCategory;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { id: "this_machine", label: "This machine", icon: HardDrive },
  { id: "s3_compatible", label: "S3-compatible object storage", icon: Cloud },
  { id: "nextcloud_webdav", label: "Nextcloud and WebDAV", icon: Network },
  { id: "nas_sftp", label: "NAS over SFTP", icon: Server },
];

export type ProviderValues = StorageProviderConfigValues;

export function defaultProviderValues(provider: StorageProvider): ProviderValues {
  return providerDefaults(provider);
}

function tierLabel(tier: string): string {
  return tier.charAt(0).toUpperCase() + tier.slice(1);
}

function supportLabel(level: string | undefined): string {
  return (level ?? "stable").charAt(0).toUpperCase() + (level ?? "stable").slice(1);
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
}) {
  const i18n = useOptionalI18n();
  const selected = props.providers.find((provider) => provider.id === props.providerId);
  const [categoryOverride, setCategoryOverride] = useState<ProviderCategory | null>(null);
  const selectedCategory = categoryOverride ?? selected?.category ?? "this_machine";
  const secretFieldsSet = new Set(
    Array.isArray(props.values.secret_fields_set) ? props.values.secret_fields_set : [],
  );
  const consequences = selected
    ? providerConsequences(
        selected,
        i18n?.t("storage.guardedCatalog") ?? "Confirmed catalog removal retains stored bytes.",
        i18n?.t("storage.guardedRetention") ?? "Automatic physical deletion is unavailable.",
      )
    : [];

  return (
    <div className="space-y-5">
      <fieldset className="space-y-2">
        <legend className="text-xs font-mono uppercase tracking-wider text-on-surface-variant">
          Storage category
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
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
                variant="outline"
                disabled={props.disabled || !hasProviders}
                aria-pressed={active}
                onClick={() => setCategoryOverride(category.id)}
                className={cn(
                  "h-auto justify-start gap-2 whitespace-normal px-3 py-3 text-left",
                  active && "border-transparent bg-accent text-accent-foreground hover:bg-accent",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden />
                {category.label}
              </Button>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs font-mono uppercase tracking-wider text-on-surface-variant">
          Provider
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {props.providers
            .filter((provider) => provider.category === selectedCategory)
            .map((provider) => (
              <Button
                key={provider.id}
                type="button"
                variant="outline"
                disabled={props.disabled || !provider.selectable}
                aria-pressed={provider.id === props.providerId}
                title={
                  provider.uses?.vault && !provider.uses.vault.available
                    ? storageOperationMessage(provider.uses.vault.reason, i18n?.t)
                    : (provider.disabled_reason ?? undefined)
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
                  <span className="block text-sm font-medium">{provider.label}</span>
                  <span className="block text-xs font-normal opacity-70">
                    {provider.uses?.vault && !provider.uses.vault.available
                      ? storageOperationMessage(provider.uses.vault.reason, i18n?.t)
                      : (provider.disabled_reason ?? provider.description)}
                  </span>
                </span>
              </Button>
            ))}
        </div>
      </fieldset>

      {selected && (
        <section className="space-y-4 rounded-lg border border-outline-variant bg-surface-container-low p-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">
                {i18n?.t("settings.storageSupport", {
                  level: supportLabel(selected.support_level),
                }) ?? `Support: ${supportLabel(selected.support_level)}`}
              </Badge>
              <Badge variant="secondary">Expected: {tierLabel(selected.expected_tier)}</Badge>
              {props.activeTier && (
                <Badge variant="outline">Active: {tierLabel(props.activeTier)}</Badge>
              )}
            </div>
            <p className="text-sm text-on-surface">{selected.expected_tier_note}</p>
            {selected.expected_tier === "guarded" && (
              <p className="text-xs font-medium text-on-surface">Guarded storage consequences</p>
            )}
            {consequences.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-xs text-on-surface-variant">
                {consequences.map((consequence) => (
                  <li key={consequence}>{consequence}</li>
                ))}
              </ul>
            )}
          </div>

          <StorageProviderFields
            provider={selected}
            values={props.values}
            onChange={props.onValueChange}
            disabled={props.disabled}
            storedSecrets={[...secretFieldsSet]}
          />
        </section>
      )}
    </div>
  );
}
