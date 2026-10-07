import { useState } from "react";
import {
  defaultProviderValues,
  StorageProviderPicker,
  type ProviderValues,
} from "@/components/storage-provider-picker";
import { Button } from "@/components/ui/button";
import { useQuery } from "@tanstack/react-query";
import { storageProvidersOptions } from "@/lib/queries/settings-storage";
import { vaultConfigOptions } from "@/lib/queries/settings-config";
import { usePrepareGuideStorage } from "@/features/setup/guide";
import { useI18n } from "@/lib/i18n";
import { setupErrorMessage, setupStorageBody } from "@/lib/setup-storage";
import { storageOperationMessage } from "@/lib/storage-operations";
import { providerFormError } from "@/lib/storage-provider-form";
import type { StorageProvider } from "@/types";

/**
 * The storage step for an owner provisioned from VAULT_SETUP_ADMIN_*.
 *
 * Browser registration chooses storage before the account exists; this owner
 * signed in first, so the same choice happens here. The server checks the
 * choice before persisting it, so a mistyped remote setting can be corrected.
 */
export function SetupStorageChoice({ onPrepared }: { onPrepared: () => void }) {
  const { t } = useI18n();
  const catalog = useQuery(storageProvidersOptions());
  const config = useQuery(vaultConfigOptions());
  const prepare = usePrepareGuideStorage();
  const providers = catalog.data ?? [];
  const loaded = catalog.isSuccess && config.isSuccess;
  const localRoots: ProviderValues = config.data
    ? { data_dir: config.data.data_dir, thumb_dir: config.data.thumb_dir }
    : {};
  const [draft, setDraft] = useState<{ providerId: string; values: ProviderValues } | null>(null);
  const providerId = draft?.providerId ?? "local";
  const values =
    draft?.values ??
    valuesFor(
      providers.find((item) => item.id === "local"),
      localRoots,
    );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const message = error || (catalog.isError || config.isError ? t("setup.failed") : "");

  async function submit() {
    const provider = providers.find((item) => item.id === providerId);
    const invalid = !provider?.selectable
      ? provider?.disabled_reason
        ? storageOperationMessage(provider.disabled_reason, t)
        : t("setup.providerUnavailable")
      : providerFormError(provider, values);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await prepare(setupStorageBody(providerId, values));
      onPrepared();
    } catch (failure) {
      if (failure instanceof DOMException && failure.name === "AbortError") return;
      setError(setupErrorMessage(failure instanceof Error ? failure : new Error(), t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      noValidate
      className="space-y-5"
      aria-label={t("setup.files")}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="text-sm leading-relaxed text-muted-foreground">
        {t("setup.chooseStorageHelp")}
      </p>
      {loaded && (
        <StorageProviderPicker
          onboarding
          disabled={busy}
          providers={providers}
          providerId={providerId}
          values={values}
          onProviderChange={(provider) => {
            setDraft({ providerId: provider.id, values: valuesFor(provider, localRoots) });
            setError("");
          }}
          onValueChange={(name, value) => {
            setDraft({ providerId, values: { ...values, [name]: value } });
            setError("");
          }}
        />
      )}
      {message && (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      )}
      <Button type="submit" className="h-11 w-full" loading={busy} disabled={!loaded}>
        {t("setup.chooseStorage")}
      </Button>
    </form>
  );
}

/** A provider's defaults; local storage starts at the deployment's own roots. */
function valuesFor(
  provider: StorageProvider | undefined,
  localRoots: ProviderValues,
): ProviderValues {
  if (!provider) return {};
  const values = { ...defaultProviderValues(provider) };
  if (provider.id === "local") Object.assign(values, localRoots);
  return values;
}
