import { messageCatalogs, type MessageKey } from "@/lib/i18n";

const REASONS = {
  storage_dependency_missing: "storage.fullImageRequired",
  storage_service_not_compiled: "storage.serviceMissing",
  storage_use_unsupported: "storage.useUnsupported",
  storage_endpoint_probe_required: "storage.probeRequired",
  storage_exact_delete_unavailable: "storage.deleteUnsupported",
  storage_retention_unsupported: "storage.retentionUnsupported",
  storage_catalog_only_bytes_retained: "storage.catalogOnly",
  storage_source_originals_retained: "storage.sourceOriginalsRetained",
  storage_source_read_only: "storage.sourceReadOnly",
  storage_independent_backup_required: "storage.independentBackupRequired",
  storage_gc_verification_required: "storage.gcVerificationRequired",
  storage_gc_witness_unsupported: "storage.gcWitnessUnsupported",
  storage_backup_ownership_required: "storage.backupOwnershipRequired",
  storage_owned_objects_verified: "storage.ownedObjectsVerified",
} satisfies Record<string, MessageKey>;

export function storageOperationMessage(
  reason: string,
  translate: (key: MessageKey) => string = (key) => messageCatalogs.en[key],
): string {
  return translate(
    Object.entries(REASONS).find(([code]) => code === reason)?.[1] ??
      "storage.operationUnavailable",
  );
}
