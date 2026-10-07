import { captureEditingBase } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { useRef } from "react";
import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createFilamentProfile,
  deleteFilamentProfile,
  listFilamentProfiles,
  updateFilamentProfile,
} from "@/lib/api/filaments";
import {
  createPrinterProfile,
  deletePrinterProfile,
  listPrinterProfiles,
  updatePrinterProfile,
} from "@/lib/api/printer-profiles";
import { syncSpoolmanFilaments } from "@/lib/api/spoolman";
import { queryKeys } from "@/lib/query-client";
import { requireSessionVersion } from "@/lib/session-transport";
import type { FilamentProfileRead, PrinterProfileRead } from "@/types";

export const profileKeys = {
  filaments: queryKeys.filamentProfiles,
  printers: queryKeys.printerProfiles,
};
export function filamentProfilesOptions(read: typeof listFilamentProfiles = listFilamentProfiles) {
  return queryOptions({
    queryKey: profileKeys.filaments,
    queryFn: ({ signal }) => read({ signal }),
  });
}
export function printerProfilesOptions(read: typeof listPrinterProfiles = listPrinterProfiles) {
  return queryOptions({
    queryKey: profileKeys.printers,
    queryFn: ({ signal }) => read({ signal }),
  });
}

type ProfileRow = { kind: "filament" | "printer"; id: number };
type MutationContext = { session: number; row: ProfileRow | null };

/** Catalog commands publish server DTOs and coordinate reads across every consumer. */
export function useProfileCommands() {
  const client = useQueryClient();
  const pending = useRef({ filament: new Set<number>(), printer: new Set<number>() });
  const cancel = () =>
    Promise.all([
      client.cancelQueries({ queryKey: profileKeys.filaments }),
      client.cancelQueries({ queryKey: profileKeys.printers }),
    ]);
  const prepare = async (
    session: number,
    row: ProfileRow | null = null,
  ): Promise<MutationContext> => {
    requireSessionVersion(session);
    if (row && pending.current[row.kind].has(row.id))
      throw new Error("Profile row is already saving");
    if (row) pending.current[row.kind].add(row.id);
    try {
      await cancel();
      requireSessionVersion(session);
      return { session, row };
    } catch (error) {
      if (row) pending.current[row.kind].delete(row.id);
      throw error;
    }
  };
  const assertSession = (context: MutationContext | undefined) => {
    if (!context) throw new Error("Profile mutation session context is required");
    requireSessionVersion(context.session);
  };
  const settled = (context: MutationContext | undefined) => {
    if (context?.row) pending.current[context.row.kind].delete(context.row.id);
  };
  const publishFilament = async (
    row: FilamentProfileRead,
    context: MutationContext | undefined,
    append: boolean,
  ) => {
    assertSession(context);
    await cancel();
    assertSession(context);
    client.setQueryData<FilamentProfileRead[]>(profileKeys.filaments, (rows) => {
      assertSession(context);
      if (!rows) return rows;
      const found = rows.some((item) => item.id === row.id);
      return found
        ? rows.map((item) =>
            item.id === row.id &&
            item.edit_epoch === row.edit_epoch &&
            item.edit_version <= row.edit_version
              ? row
              : item,
          )
        : append
          ? [...rows, row]
          : rows;
    });
    assertSession(context);
    void client.invalidateQueries({ queryKey: profileKeys.filaments });
  };
  const publishPrinter = async (
    row: PrinterProfileRead,
    context: MutationContext | undefined,
    append: boolean,
  ) => {
    assertSession(context);
    await cancel();
    assertSession(context);
    client.setQueryData<PrinterProfileRead[]>(profileKeys.printers, (rows) => {
      assertSession(context);
      if (!rows) return rows;
      const found = rows.some((item) => item.id === row.id);
      return found
        ? rows.map((item) =>
            item.id === row.id &&
            item.edit_epoch === row.edit_epoch &&
            item.edit_version <= row.edit_version
              ? row
              : item,
          )
        : append
          ? [...rows, row]
          : rows;
    });
    assertSession(context);
    void client.invalidateQueries({ queryKey: profileKeys.printers });
  };
  return {
    reviewFilament: async (id: number, session: number) => {
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: profileKeys.filaments, exact: true });
      requireSessionVersion(session);
      const rows = await client.fetchQuery({
        ...filamentProfilesOptions(),
        staleTime: 0,
        retry: false,
      });
      requireSessionVersion(session);
      const row = rows.find((item) => item.id === id);
      if (!row) throw new Error("filament_profile_not_found");
      captureEditingBase(row);
      return row;
    },
    reviewPrinter: async (id: number, session: number) => {
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: profileKeys.printers, exact: true });
      requireSessionVersion(session);
      const rows = await client.fetchQuery({
        ...printerProfilesOptions(),
        staleTime: 0,
        retry: false,
      });
      requireSessionVersion(session);
      const row = rows.find((item) => item.id === id);
      if (!row) throw new Error("printer_profile_not_found");
      captureEditingBase(row);
      return row;
    },
    createFilament: useMutation({
      mutationFn: ({
        payload,
        session,
      }: {
        payload: Parameters<typeof createFilamentProfile>[0];
        session: number;
      }) => {
        requireSessionVersion(session);
        return createFilamentProfile(payload);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: (row, _, context) => publishFilament(row, context, true),
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
    updateFilament: useMutation({
      mutationFn: ({
        id,
        payload,
        base,
        session,
      }: {
        id: number;
        payload: Parameters<typeof updateFilamentProfile>[1];
        base: EditingBase;
        session: number;
      }) => {
        requireSessionVersion(session);
        return updateFilamentProfile(id, payload, { base });
      },
      onMutate: ({ id, session }) => prepare(session, { kind: "filament", id }),
      onSuccess: (row, _, context) => publishFilament(row, context, false),
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
    removeFilament: useMutation({
      mutationFn: ({ id, session }: { id: number; session: number }) => {
        requireSessionVersion(session);
        return deleteFilamentProfile(id);
      },
      onMutate: ({ id, session }) => prepare(session, { kind: "filament", id }),
      onSuccess: async (_, { id }, context) => {
        assertSession(context);
        await cancel();
        assertSession(context);
        client.setQueryData<FilamentProfileRead[]>(profileKeys.filaments, (rows) => {
          assertSession(context);
          return rows?.filter((row) => row.id !== id);
        });
        assertSession(context);
        void client.invalidateQueries({ queryKey: profileKeys.filaments });
      },
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
    createPrinter: useMutation({
      mutationFn: ({
        payload,
        session,
      }: {
        payload: Parameters<typeof createPrinterProfile>[0];
        session: number;
      }) => {
        requireSessionVersion(session);
        return createPrinterProfile(payload);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: (row, _, context) => publishPrinter(row, context, true),
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
    updatePrinter: useMutation({
      mutationFn: ({
        id,
        payload,
        base,
        session,
      }: {
        id: number;
        payload: Parameters<typeof updatePrinterProfile>[1];
        base: EditingBase;
        session: number;
      }) => {
        requireSessionVersion(session);
        return updatePrinterProfile(id, payload, { base });
      },
      onMutate: ({ id, session }) => prepare(session, { kind: "printer", id }),
      onSuccess: (row, _, context) => publishPrinter(row, context, false),
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
    removePrinter: useMutation({
      mutationFn: ({ id, session }: { id: number; session: number }) => {
        requireSessionVersion(session);
        return deletePrinterProfile(id);
      },
      onMutate: ({ id, session }) => prepare(session, { kind: "printer", id }),
      onSuccess: async (_, { id }, context) => {
        assertSession(context);
        await cancel();
        assertSession(context);
        client.setQueryData<PrinterProfileRead[]>(profileKeys.printers, (rows) => {
          assertSession(context);
          return rows?.filter((row) => row.id !== id);
        });
        assertSession(context);
        void client.invalidateQueries({ queryKey: profileKeys.printers });
      },
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
    syncSpoolman: useMutation({
      mutationFn: (session: number) => {
        requireSessionVersion(session);
        return syncSpoolmanFilaments();
      },
      onMutate: (session) => prepare(session),
      onSuccess: async (_, _variables, context) => {
        assertSession(context);
        await cancel();
        assertSession(context);
        await client.invalidateQueries({ queryKey: profileKeys.filaments });
        assertSession(context);
      },
      onSettled: (_, _error, _variables, context) => settled(context),
    }),
  };
}
