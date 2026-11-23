import type { MessageKey } from "@/lib/locale";
import type { SetupStorageRequest, StorageProviderConfigValues } from "@/types";

/**
 * The storage payload both setup flows send: browser registration, and the
 * owner provisioned from VAULT_SETUP_ADMIN_* choosing storage after sign-in.
 * Blank fields are omitted so the server applies its own defaults.
 */
export function setupStorageBody(
  providerId: string,
  values: StorageProviderConfigValues,
): SetupStorageRequest {
  return {
    storage_provider: providerId,
    storage_provider_config: {
      provider: providerId,
      ...Object.fromEntries(
        Object.entries(values).filter(
          ([key, value]) => key !== "secret_fields_set" && value !== "",
        ),
      ),
    },
  };
}

/** Map a setup API failure to the sentence that tells the user what to change. */
export function setupErrorMessage(error: Error, translate: (key: MessageKey) => string): string {
  const raw = error.message;
  if (/already_configured|users_already_exist/.test(raw)) return translate("setup.exists");
  if (/not_empty/.test(raw)) return translate("setup.populated");
  if (/setup_session/.test(raw)) return translate("setup.session");
  if (/setup_origin/.test(raw)) return translate("setup.origin");
  if (/setup_disabled/.test(raw)) return translate("setup.disabled");
  if (/setup_remote_storage/.test(raw)) return translate("setup.remoteFailure");
  if (/dir|path|writable|readable/.test(raw)) return translate("setup.paths");
  return translate("setup.failed");
}

/**
 * The local folder fields a failed storage check is about, so the form can
 * mark them. A failure naming one folder marks that folder; a path failure
 * naming neither marks both; anything else (sessions, remote access) marks none.
 */
export function setupErrorFields(error: Error): ("data_dir" | "thumb_dir")[] {
  const raw = error.message;
  const named = (["data_dir", "thumb_dir"] as const).filter((field) => raw.includes(field));
  if (named.length > 0) return named;
  if (/setup_|already_configured|users_already_exist/.test(raw)) return [];
  return /not_empty|dir|path|writable|readable/.test(raw) ? ["data_dir", "thumb_dir"] : [];
}
