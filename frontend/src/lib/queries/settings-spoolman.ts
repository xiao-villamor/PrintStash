/** Spoolman settings own conditional writes and masked receipts; secrets remain local. */
import { useEffect, useRef } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/spoolman";
import { queryKeys } from "@/lib/query-client";
import { onAuthChange } from "@/lib/auth-store";
import { requireSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import { captureEditingBase } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import type { SpoolmanStatus, SpoolmanUpdate } from "@/types";

export function spoolmanStatusOptions(read = api.getSpoolmanStatus) {
  return queryOptions({
    queryKey: queryKeys.spoolmanStatus,
    queryFn: ({ signal }) => read({ signal }),
    retry: false,
  });
}

export function useSpoolmanCommands(canEdit: boolean) {
  const client = useQueryClient();
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    if (!canEdit) active.current?.abort();
    const unsubscribe = onAuthChange(() => active.current?.abort());
    return () => {
      live.current = false;
      active.current?.abort();
      unsubscribe();
    };
  }, [canEdit]);
  async function run<T>(
    session: number,
    operation: (signal: AbortSignal, current: () => void) => Promise<T>,
  ): Promise<T> {
    requireSessionVersion(session);
    if (!live.current || !canEdit) throw new DOMException("Spoolman editor retired", "AbortError");
    if (active.current) throw new Error("Spoolman command already pending");
    const controller = new AbortController();
    active.current = controller;
    const current = () => {
      requireSessionVersion(session);
      controller.signal.throwIfAborted();
    };
    try {
      const result = await operation(controller.signal, current);
      current();
      return result;
    } catch (error) {
      current();
      if ([401, 403].includes(parseApiError(error).status))
        await client.invalidateQueries({ queryKey: queryKeys.spoolmanStatus, exact: true });
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  return {
    save: (body: SpoolmanUpdate, base: EditingBase, session: number) =>
      run(session, async (signal, current) => {
        const captured = captureEditingBase(base);
        await client.cancelQueries({ queryKey: queryKeys.spoolmanStatus, exact: true });
        current();
        const row = await api.updateSpoolman(body, { base: captured, signal });
        current();
        await client.cancelQueries({ queryKey: queryKeys.spoolmanStatus, exact: true });
        current();
        client.setQueryData<SpoolmanStatus>(queryKeys.spoolmanStatus, (previous) =>
          previous &&
          previous.edit_epoch === row.edit_epoch &&
          previous.edit_version <= row.edit_version
            ? row
            : previous,
        );
        void client.invalidateQueries({ queryKey: queryKeys.spools });
        return row;
      }),
    adopt: (session: number) =>
      run(session, async (_signal, current) => {
        await client.cancelQueries({ queryKey: queryKeys.spoolmanStatus, exact: true });
        current();
        const snapshot = await client.fetchQuery({ ...spoolmanStatusOptions(), staleTime: 0 });
        current();
        return snapshot;
      }),
    review: (session: number) => run(session, async (signal) => api.getSpoolmanStatus({ signal })),
    test: (body: Parameters<typeof api.testSpoolman>[0], session: number) =>
      run(session, async (signal) => api.testSpoolman(body, { signal })),
  };
}
