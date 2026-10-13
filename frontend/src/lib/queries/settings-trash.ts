/** Trash rows and durable GC plans have independent reads and concrete safety commands. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { listTrash, restoreModel, purgeModel } from "@/lib/api/models";
import {
  getActiveGcPlan,
  createGcPlan,
  approveGcPlan,
  abortGcPlan,
  finalizeGcPlan,
  type GcPlan,
} from "@/lib/api/gc";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError, parseApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import type { TrashedModelRead, TrashPurgeRead } from "@/types";

export const settingsTrashKeys = {
  rows: ["settings-trash", "models"] as const,
  plan: ["settings-trash", "gc"] as const,
};
export function settingsTrashOptions() {
  return queryOptions({
    queryKey: settingsTrashKeys.rows,
    queryFn: ({ signal }) => listTrash({ signal }),
    retry: false,
  });
}
export function settingsGcOptions() {
  return queryOptions({
    queryKey: settingsTrashKeys.plan,
    queryFn: ({ signal }) => getActiveGcPlan({ signal }),
    retry: false,
  });
}
export type TrashCommand = { session: number } & (
  | { kind: "restore"; target: TrashedModelRead }
  | { kind: "purge"; target: TrashedModelRead; confirmStorageRisk: boolean }
  | { kind: "preview" }
  | { kind: "approve"; plan: GcPlan; digest: string }
  | { kind: "abort" | "finalize"; plan: GcPlan }
);
export type TrashReceipt =
  | { kind: "restored" }
  | { kind: "purged"; result: TrashPurgeRead }
  | { kind: "plan"; plan: GcPlan };

export function useSettingsTrashCommand() {
  const client = useQueryClient();
  const { user } = useAuth();
  const authority = useRef(user);
  useLayoutEffect(() => {
    authority.current = user;
  }, [user]);
  const [session] = useState(getSessionVersion);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => active.current?.abort());
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, []);
  async function run(command: TrashCommand): Promise<TrashReceipt> {
    requireSessionVersion(session);
    requireSessionVersion(command.session);
    if (!live.current || active.current)
      throw new DOMException("Trash view unavailable", "AbortError");
    const request = new AbortController();
    active.current = request;
    const gc = command.kind !== "restore" && command.kind !== "purge";
    const queryKey = gc ? settingsTrashKeys.plan : settingsTrashKeys.rows;
    const before = client.getQueryData(queryKey);
    function current() {
      requireSessionVersion(session);
      requireSessionVersion(command.session);
      request.signal.throwIfAborted();
      if (!live.current) throw new DOMException("Trash view disposed", "AbortError");
      if (!authority.current || (gc && !authority.current.is_superuser))
        throw new ApiError(403, "forbidden", "forbidden");
    }
    function eligible() {
      const state = client.getQueryState(queryKey);
      if (state?.status === "error") throw parseApiError(state.error);
      if (state?.data === undefined)
        throw new ApiError(409, "trash_read_required", "trash_read_required");
      if ("target" in command) {
        const row = client
          .getQueryData<TrashedModelRead[]>(queryKey)
          ?.find((item) => item.id === command.target.id);
        if (!row || row.deleted_at !== command.target.deleted_at)
          throw new ApiError(409, "trash_target_changed", "trash_target_changed");
      } else if ("plan" in command) {
        const plan = client.getQueryData<GcPlan | null>(queryKey);
        if (
          !plan ||
          plan.id !== command.plan.id ||
          plan.digest !== command.plan.digest ||
          plan.state !== command.plan.state
        )
          throw new ApiError(409, "gc_plan_changed", "gc_plan_changed");
      }
    }
    try {
      current();
      eligible();
      await client.cancelQueries({ queryKey, exact: true });
      current();
      eligible();
      const options = { signal: request.signal };
      let receipt: TrashReceipt;
      switch (command.kind) {
        case "restore":
          await restoreModel(command.target.id, options);
          receipt = { kind: "restored" };
          break;
        case "purge":
          receipt = {
            kind: "purged",
            result: await purgeModel(command.target.id, command.confirmStorageRisk, options),
          };
          break;
        case "preview": {
          let plan: GcPlan;
          try {
            plan = await createGcPlan(options);
          } catch (error) {
            current();
            const problem = parseApiError(error);
            if (problem.status !== 409 || problem.code !== "gc_plan_active") throw error;
            const existing = await getActiveGcPlan(options);
            if (!existing) throw error;
            plan = existing;
          }
          receipt = { kind: "plan", plan };
          break;
        }
        case "approve":
          receipt = {
            kind: "plan",
            plan: await approveGcPlan(command.plan.id, command.digest, options),
          };
          break;
        case "abort":
          receipt = { kind: "plan", plan: await abortGcPlan(command.plan.id, options) };
          break;
        case "finalize":
          receipt = { kind: "plan", plan: await finalizeGcPlan(command.plan.id, options) };
          break;
      }
      current();
      await client.cancelQueries({ queryKey, exact: true });
      current();
      const observed = client.getQueryData(queryKey);
      if (observed === before) {
        if (receipt.kind === "plan") client.setQueryData(queryKey, receipt.plan);
        else if ("target" in command)
          client.setQueryData<TrashedModelRead[]>(queryKey, (rows) =>
            rows?.filter((row) => row.id !== command.target.id),
          );
      } else {
        // No edit version exists for these projections. A receipt must not replace
        // an intervening observation; re-read the server to settle membership/state.
        void client.invalidateQueries({ queryKey, exact: true });
      }
      if (!gc || command.kind === "finalize") {
        current();
        for (const key of [
          queryKeys.models,
          queryKeys.collections,
          queryKeys.multipartModels,
          queryKeys.outliner,
          queryKeys.vaultStats,
        ])
          void client.invalidateQueries({ queryKey: key });
        if (command.kind === "finalize")
          void client.invalidateQueries({ queryKey: settingsTrashKeys.rows, exact: true });
      }
      return receipt;
    } catch (error) {
      current();
      if ([401, 403, 404].includes(parseApiError(error).status))
        void client.invalidateQueries({ queryKey, exact: true });
      throw error;
    } finally {
      if (active.current === request) active.current = null;
    }
  }
  return { run };
}
