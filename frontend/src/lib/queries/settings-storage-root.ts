/** Explicit legacy-root enrollment keeps its reviewed role/path and private-session lifetime. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { enrollStorageRoot } from "@/lib/api/config";
import { onAuthChange } from "@/lib/auth-store";
import { useAuth } from "@/lib/auth-context";
import { ApiError, parseApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { StorageRootRole, VaultConfigRead } from "@/types";

export interface ReviewedStorageRoot {
  role: StorageRootRole;
  path: string;
}
export function useStorageRootEnrollment() {
  const client = useQueryClient();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const [session] = useState(getSessionVersion);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      setPending(false);
    });
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, []);
  async function enroll(target: ReviewedStorageRoot) {
    requireSessionVersion(session);
    if (!live.current || active.current)
      throw new DOMException("Storage enrollment unavailable", "AbortError");
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    function assertCurrent() {
      requireSessionVersion(session);
      controller.signal.throwIfAborted();
      if (!live.current) throw new DOMException("Storage view disposed", "AbortError");
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    }
    function assertReviewed() {
      assertCurrent();
      const projection = client.getQueryState<VaultConfigRead>(queryKeys.vaultConfig);
      if (projection?.status === "error") throw projection.error;
      const cfg = projection?.data;
      const path = target.role === "data" ? cfg?.data_dir : cfg?.thumb_dir;
      if (!cfg || cfg.storage_backend !== "local" || path !== target.path)
        throw new ApiError(409, "storage_review_changed", "storage_review_changed");
    }
    try {
      assertReviewed();
      const receipt = await enrollStorageRoot(target.role, target.path, {
        signal: controller.signal,
      });
      assertCurrent();
      return receipt;
    } catch (error) {
      if (
        session === getSessionVersion() &&
        !controller.signal.aborted &&
        [401, 403, 404].includes(parseApiError(error).status)
      )
        void client.invalidateQueries({ queryKey: queryKeys.vaultConfig, exact: true });
      throw error;
    } finally {
      if (active.current === controller) {
        active.current = null;
        if (live.current && session === getSessionVersion()) setPending(false);
      }
    }
  }
  return { enroll, pending };
}
