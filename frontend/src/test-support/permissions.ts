import type { CollectionPermissionRead, PrinterPermissionRead } from "@/types";

export function aCollectionPermission(
  over: Partial<CollectionPermissionRead> = {},
): CollectionPermissionRead {
  return {
    collection_id: 5,
    user_id: 2,
    username: "maker",
    role: "edit",
    inherited: false,
    ...over,
  };
}

export function aPrinterPermission(
  over: Partial<PrinterPermissionRead> = {},
): PrinterPermissionRead {
  return {
    id: 11,
    printer_id: 4,
    user_id: 2,
    username: "maker",
    role: "print",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}
