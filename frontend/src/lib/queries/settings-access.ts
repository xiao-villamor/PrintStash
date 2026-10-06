/** Direct grants have one resource owner; choices and unsaved roles remain local. */
import { queryOptions, useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import {
  listCollectionPermissions,
  updateCollectionPermission,
  deleteCollectionPermission,
} from "@/lib/api/taxonomy";
import {
  listPrinterPermissions,
  updatePrinterPermission,
  deletePrinterPermission,
} from "@/lib/api/printers";
import { requireSessionVersion, getSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import type {
  CollectionRole,
  CollectionPermissionRead,
  PrinterRole,
  PrinterPermissionRead,
  PrinterRead,
} from "@/types";

export const accessKeys = {
  collection: (actorId: number | null, id: number | null) =>
    ["collection-permissions", actorId, id] as const,
  printer: (actorId: number | null, id: number) => ["printer-permissions", actorId, id] as const,
};
export function collectionAccessOptions(actorId: number | null, id: number | null) {
  return queryOptions({
    queryKey: accessKeys.collection(actorId, id),
    queryFn: ({ signal }) => {
      if (actorId === null || id === null)
        throw new Error("Collection access requires an authorized selection");
      return listCollectionPermissions(id, { signal });
    },
    enabled: actorId !== null && id !== null,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}
export function printerAccessOptions(actorId: number | null, id: number, enabled = true) {
  return queryOptions({
    queryKey: accessKeys.printer(actorId, id),
    queryFn: ({ signal }) => {
      if (actorId === null) throw new Error("Printer access requires an authorized reader");
      return listPrinterPermissions(id, { signal });
    },
    enabled: actorId !== null && enabled,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}
export function accessDenied(error: Error): boolean {
  return [401, 403, 404].includes(parseApiError(error).status);
}
/** Individual printer failures cannot erase another printer's acknowledged grants. */
export function usePrinterAccess(
  actorId: number | null,
  printers: PrinterRead[],
  enabled: boolean,
) {
  const reads = useQueries({
    queries: printers.map((printer) => printerAccessOptions(actorId, printer.id, enabled)),
  });
  return reads.map((query, index) => ({ printer: printers[index], query }));
}
interface AccessGesture {
  actorId: number;
  session: number;
  targetUserId: number;
}
export type CollectionAccessCommand = AccessGesture & { collectionId: number } & (
    | { kind: "grant"; role: CollectionRole }
    | { kind: "revoke" }
  );
export function useCollectionAccessCommand() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    onMutate: async (command: CollectionAccessCommand) => {
      requireSessionVersion(command.session);
      await client.cancelQueries({
        queryKey: accessKeys.collection(command.actorId, command.collectionId),
        exact: true,
      });
      requireSessionVersion(command.session);
    },
    mutationFn: async (
      command: CollectionAccessCommand,
    ): Promise<CollectionPermissionRead | null> => {
      requireSessionVersion(command.session);
      if (command.kind === "grant")
        return updateCollectionPermission(command.collectionId, command.targetUserId, {
          role: command.role,
        });
      await deleteCollectionPermission(command.collectionId, command.targetUserId);
      return null;
    },
    onSuccess: async (row, command) => {
      requireSessionVersion(command.session);
      const key = accessKeys.collection(command.actorId, command.collectionId);
      await client.cancelQueries({ queryKey: key, exact: true });
      requireSessionVersion(command.session);
      client.setQueryData<CollectionPermissionRead[]>(key, (rows) => {
        requireSessionVersion(command.session);
        if (!rows) return rows;
        const remaining = rows.filter((item) => item.user_id !== command.targetUserId);
        return row
          ? [...remaining, row].sort((a, b) => a.username.localeCompare(b.username))
          : remaining;
      });
    },
    onError: (_error, command) => {
      if (command.session === getSessionVersion())
        void client.invalidateQueries({
          queryKey: accessKeys.collection(command.actorId, command.collectionId),
          exact: true,
        });
    },
  });
}
export type PrinterAccessCommand = AccessGesture & { printerId: number } & (
    | { kind: "grant"; role: PrinterRole }
    | { kind: "revoke" }
  );
export function usePrinterAccessCommand() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    onMutate: async (command: PrinterAccessCommand) => {
      requireSessionVersion(command.session);
      await client.cancelQueries({
        queryKey: accessKeys.printer(command.actorId, command.printerId),
        exact: true,
      });
      requireSessionVersion(command.session);
    },
    mutationFn: async (command: PrinterAccessCommand): Promise<PrinterPermissionRead | null> => {
      requireSessionVersion(command.session);
      if (command.kind === "grant")
        return updatePrinterPermission(command.printerId, command.targetUserId, command.role);
      await deletePrinterPermission(command.printerId, command.targetUserId);
      return null;
    },
    onSuccess: async (row, command) => {
      requireSessionVersion(command.session);
      const key = accessKeys.printer(command.actorId, command.printerId);
      await client.cancelQueries({ queryKey: key, exact: true });
      requireSessionVersion(command.session);
      client.setQueryData<PrinterPermissionRead[]>(key, (rows) => {
        requireSessionVersion(command.session);
        if (!rows) return rows;
        const remaining = rows.filter((item) => item.user_id !== command.targetUserId);
        return row
          ? [...remaining, row].sort((a, b) => a.username.localeCompare(b.username))
          : remaining;
      });
    },
    onError: (_error, command) => {
      if (command.session === getSessionVersion())
        void client.invalidateQueries({
          queryKey: accessKeys.printer(command.actorId, command.printerId),
          exact: true,
        });
    },
  });
}
