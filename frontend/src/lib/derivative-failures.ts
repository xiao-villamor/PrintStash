/**
 * Why a preview, metadata or 3D view could not be produced, in the few causes a
 * reader can act on.
 *
 * Producers report precise codes (a worker that died, an unreadable 3MF, a
 * missing blob). Readers need the cause and the next step, not the code, and an
 * unrecognised code must never read as a network problem: the page that shows
 * it was served by the same server that recorded it.
 */
import { uiMessage, uiText, type MessageDescriptor } from "@/lib/locale";

export type DerivativeFailureCause =
  | "file"
  | "too_large"
  | "processing"
  | "storage"
  | "storage_credentials_expired"
  | "storage_authentication_failed"
  | "storage_access_denied"
  | "storage_signature_invalid"
  | "unknown";

const CAUSES = {
  invalid_source: "file",
  unsupported_format: "file",
  unsupported_capability: "file",
  no_geometry: "file",
  source_changed: "file",
  invalid_3mf: "file",
  project_profile_malformed: "file",
  project_profile_limits: "too_large",
  tessellation_failed: "file",
  resource_limit: "too_large",
  scene_resource_limit: "too_large",
  timeout: "too_large",
  worker_failed: "processing",
  renderer_no_output: "processing",
  storage: "storage",
  source_unavailable: "storage",
  backup_blob_missing: "storage",
  remote_storage_metadata_failed: "storage",
  remote_storage_read_failed: "storage",
  remote_storage_list_failed: "storage",
  storage_credentials_expired: "storage_credentials_expired",
  storage_authentication_failed: "storage_authentication_failed",
  storage_access_denied: "storage_access_denied",
  storage_signature_invalid: "storage_signature_invalid",
} as const satisfies Record<string, DerivativeFailureCause>;

function isKnownReason(reason: string): reason is keyof typeof CAUSES {
  return Object.hasOwn(CAUSES, reason);
}

export function derivativeFailureCause(reason: string | null): DerivativeFailureCause {
  return reason !== null && isKnownReason(reason) ? CAUSES[reason] : "unknown";
}

export function derivativeFailureMessage(cause: DerivativeFailureCause): string {
  const { key, values } = derivativeFailureDetail(cause);
  return uiText(key, values);
}

export function derivativeFailureDetail(cause: DerivativeFailureCause): MessageDescriptor {
  switch (cause) {
    case "storage_credentials_expired":
    case "storage_authentication_failed":
    case "storage_access_denied":
    case "storage_signature_invalid":
      return uiMessage(cause);
    case "file":
      return uiMessage(
        "PrintStash couldn't read this file. It may be damaged, incomplete or use a feature that isn't supported yet. You can still download it.",
      );
    case "too_large":
      return uiMessage("This file is too large or complex to process. You can still download it.");
    case "processing":
      return uiMessage(
        "Processing stopped unexpectedly on the server. Try again; if it keeps failing, check the PrintStash logs.",
      );
    case "storage":
      return uiMessage(
        "PrintStash couldn't read or save this file in storage. Check that the storage is reachable, then try again.",
      );
    case "unknown":
      return uiMessage(
        "This couldn't be produced. Try again; if it keeps failing, check the PrintStash logs.",
      );
  }
}
