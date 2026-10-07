/** The full sanitized Vault configuration has one Query projection; credentials stay local. */
import { useEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { getVaultConfig, updateVaultConfig } from "@/lib/api/config";
import { queryKeys } from "@/lib/query-client";
import { parseApiError } from "@/lib/errors";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { captureEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import type { VaultConfigRead, VaultConfigUpdate } from "@/types";

export function vaultConfigOptions(reader: typeof getVaultConfig = getVaultConfig) {
  return queryOptions({
    queryKey: queryKeys.vaultConfig,
    queryFn: ({ signal }) => reader({ fresh: true, signal }),
  });
}
export interface ConfigCommand {
  session: number;
  payload: VaultConfigUpdate;
  base: EditingBase;
}
type ConfigCommandState =
  | { status: "idle" | "pending" | "success" }
  | { status: "error"; error: unknown };
/** A secret-bearing patch never becomes MutationCache variables, data or a retained closure. */
export function useVaultConfigCommand(writer: typeof updateVaultConfig = updateVaultConfig) {
  const client = useQueryClient();
  const [state, setState] = useState<ConfigCommandState>({ status: "idle" });
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      active.current = null;
      setState({ status: "idle" });
    });
    return () => {
      live.current = false;
      active.current?.abort();
      active.current = null;
      release();
    };
  }, []);
  async function mutateAsync(command: ConfigCommand): Promise<VaultConfigRead> {
    if (!live.current) throw new DOMException("Configuration view was disposed", "AbortError");
    if (active.current) throw new Error("A configuration command is already pending");
    requireSessionVersion(command.session);
    const base = captureEditingBase(command.base);
    const controller = new AbortController();
    active.current = controller;
    setState({ status: "pending" });
    try {
      await client.cancelQueries({ queryKey: queryKeys.vaultConfig, exact: true });
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
      const options: Parameters<typeof writer>[1] = { signal: controller.signal, base };
      const row = await writer(command.payload, options);
      requireEditingReceipt(row, base);
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
      await client.cancelQueries({ queryKey: queryKeys.vaultConfig, exact: true });
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
      client.setQueryData<VaultConfigRead>(queryKeys.vaultConfig, (previous) => {
        requireSessionVersion(command.session);
        if (!previous) return previous;
        if (previous.edit_epoch !== row.edit_epoch || previous.edit_version > row.edit_version)
          return previous;
        return row;
      });
      if (live.current && active.current === controller && command.session === getSessionVersion())
        setState({ status: "success" });
      return row;
    } catch (error) {
      if (
        command.session === getSessionVersion() &&
        [401, 403, 404].includes(parseApiError(error).status)
      )
        void client.invalidateQueries({ queryKey: queryKeys.vaultConfig, exact: true });
      if (live.current && active.current === controller && command.session === getSessionVersion())
        setState({ status: "error", error });
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  return {
    mutateAsync,
    isPending: state.status === "pending",
    isError: state.status === "error",
    error: state.status === "error" ? state.error : null,
  };
}
