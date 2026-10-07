import { useCallback, useEffect, useRef } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { prepareSetupStorage } from "@/lib/api/config";
import { listModelPage } from "@/lib/api/models";
import { discoverLibraryLocations } from "@/lib/api/libraries";
import { queryKeys } from "@/lib/query-client";
import { withSessionRequest } from "@/lib/session-transport";
import type { SetupStorageRequest } from "@/types";

/** This first-run preview is not a paginated Library navigation snapshot. */
export function guideModelsOptions() {
  return queryOptions({
    queryKey: [...queryKeys.models, "getting-started", { limit: 5 }],
    queryFn: ({ signal }) => listModelPage({ limit: 5 }, { signal }),
    retry: false,
  });
}
export function guideLocationsOptions() {
  return queryOptions({
    queryKey: ["library-locations"],
    queryFn: ({ signal }) => discoverLibraryLocations({ signal }),
    retry: false,
  });
}

/** Preparation belongs to this entry/session; credentials never enter MutationCache. */
export function usePrepareGuideStorage() {
  const client = useQueryClient();
  const active = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      active.current?.abort();
      active.current = null;
    },
    [],
  );
  return useCallback(
    async (body: SetupStorageRequest = {}) => {
      if (active.current) throw new Error("Storage preparation is already pending");
      const controller = new AbortController();
      active.current = controller;
      try {
        return await withSessionRequest(async (request) => {
          const receipt = await prepareSetupStorage(body, { signal: request.signal });
          request.assertCurrent();
          await client.cancelQueries({ queryKey: queryKeys.vaultConfig, exact: true });
          request.assertCurrent();
          void client.invalidateQueries({ queryKey: queryKeys.vaultConfig, exact: true });
          return receipt;
        }, controller.signal);
      } finally {
        if (active.current === controller) active.current = null;
      }
    },
    [client],
  );
}
