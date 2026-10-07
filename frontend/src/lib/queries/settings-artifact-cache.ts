/** One effective-policy/usage projection; commands retire stale reads and session receipts. */
import { useEffect, useRef, useState } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  artifactCacheApi,
  type ArtifactCachePolicy,
  type ArtifactCacheRead,
} from "@/lib/api/artifact-cache";
import { captureEditingBase } from "@/lib/api/editing";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, withSessionRequest } from "@/lib/session-transport";
import type { EditingBase } from "@/types/editing";

export const artifactCacheKey = ["artifact-cache"] as const;
export function artifactCacheOptions(api = artifactCacheApi) {
  return queryOptions({
    queryKey: artifactCacheKey,
    queryFn: async ({ signal }) => {
      const row = await api.read({ signal });
      if (!row.policy) throw new Error("Cache policy response is missing its policy");
      return row;
    },
    retry: false,
    refetchInterval: (query) => {
      if ([401, 403, 404].includes(parseApiError(query.state.error).status)) return false;
      const usage = query.state.data?.usage;
      return usage?.maintenance_running || usage?.pending_eviction_bytes ? 1000 : false;
    },
  });
}
type CacheCommand =
  | { kind: "clear" }
  | { kind: "reset"; base: EditingBase }
  | { kind: "save"; base: EditingBase; policy: ArtifactCachePolicy };
export function useArtifactCache(api = artifactCacheApi) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const [retired, setRetired] = useState(false);
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      setRetired(true);
    });
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, []);
  const current = () => live.current && session === getSessionVersion();
  const query = useQuery({ ...artifactCacheOptions(api), enabled: !retired && !busy });
  async function execute(command: CacheCommand) {
    if (!current()) throw new DOMException("Cache editor was retired", "AbortError");
    if (active.current) throw new Error("A cache command is already pending");
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    try {
      return await withSessionRequest(async (request) => {
        await client.cancelQueries({ queryKey: artifactCacheKey });
        request.assertCurrent();
        const options = { signal: request.signal };
        const receipt =
          command.kind === "clear"
            ? await api.clear(options)
            : command.kind === "reset"
              ? await api.reset({ ...options, base: captureEditingBase(command.base) })
              : await api.save(command.policy, {
                  ...options,
                  base: captureEditingBase(command.base),
                });
        request.assertCurrent();
        await client.cancelQueries({ queryKey: artifactCacheKey });
        request.assertCurrent();
        client.setQueryData<ArtifactCacheRead>(artifactCacheKey, (previous) =>
          previous &&
          (previous.edit_epoch !== receipt.edit_epoch ||
            previous.edit_version > receipt.edit_version)
            ? previous
            : receipt,
        );
        if (command.kind !== "clear")
          void client.invalidateQueries({ queryKey: queryKeys.vaultConfig });
        return receipt;
      }, controller.signal);
    } finally {
      if (active.current === controller) active.current = null;
      if (current()) setBusy(false);
    }
  }
  return { query, execute, busy, current, retired };
}
