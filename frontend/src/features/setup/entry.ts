import { queryOptions } from "@tanstack/react-query";
import {
  getSetupStatus,
  getStorageProviders,
  beginSetup,
  checkSetupStorage,
  completeSetup,
} from "@/lib/api/config";
import { ApiError } from "@/lib/errors";
import { withSessionRequest } from "@/lib/session-transport";
import type {
  SetupRequest,
  SetupResponse,
  SetupStatus,
  SetupStorageRequest,
  StorageProvider,
} from "@/types";

export interface SetupEntryApi {
  getSetupStatus: typeof getSetupStatus;
  getStorageProviders: typeof getStorageProviders;
  beginSetup: typeof beginSetup;
  checkSetupStorage: typeof checkSetupStorage;
  completeSetup: typeof completeSetup;
}
export type SetupBootstrap =
  | { kind: "configured" | "unavailable"; status: SetupStatus }
  | { kind: "ready"; status: SetupStatus; providers: StorageProvider[] };

export function setupEntryOptions(api: SetupEntryApi, entry: string, attempt: number) {
  return queryOptions({
    queryKey: ["setup-entry", entry, attempt],
    queryFn: ({ signal }): Promise<SetupBootstrap> =>
      withSessionRequest(async (request) => {
        const options = { signal: request.signal };
        const status = await api.getSetupStatus(options);
        request.assertCurrent();
        if (status.configured) return { kind: "configured", status };
        if (!status.setup_available) return { kind: "unavailable", status };
        await api.beginSetup(options);
        request.assertCurrent();
        const providers = await api.getStorageProviders(options);
        request.assertCurrent();
        return { kind: "ready", status, providers };
      }, signal),
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/** Cookie/CSRF commands deliberately keep credentials out of MutationCache. */
export function checkSetupEntry(
  api: SetupEntryApi,
  body: SetupStorageRequest,
  signal: AbortSignal,
) {
  return withSessionRequest(async (request) => {
    const options = { signal: request.signal };
    const session = await api.beginSetup(options);
    request.assertCurrent();
    const check = await api.checkSetupStorage(body, session.csrf, options);
    request.assertCurrent();
    return check;
  }, signal);
}
export type SetupCompletion =
  | { kind: "created"; response: SetupResponse }
  | { kind: "configured"; status: SetupStatus };

export function completeSetupEntry(
  api: SetupEntryApi,
  body: SetupRequest,
  signal: AbortSignal,
): Promise<SetupCompletion> {
  return withSessionRequest(async (request) => {
    const options = { signal: request.signal };
    try {
      const session = await api.beginSetup(options);
      request.assertCurrent();
      const response = await api.completeSetup(body, session.csrf, options);
      request.assertCurrent();
      return { kind: "created", response };
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) throw error;
      request.assertCurrent();
      // A lost response does not imply the database rolled back. Recovery is
      // admitted only by this same active command and cannot replay completion.
      try {
        const status = await api.getSetupStatus(options);
        request.assertCurrent();
        if (status.configured) return { kind: "configured", status };
      } catch {
        request.assertCurrent();
      }
      throw error;
    }
  }, signal);
}

/** SetupGate probes once per navigation subject; acceptance is presentation state. */
export function setupGateOptions(entry: string, navigation: number) {
  return queryOptions({
    queryKey: ["setup-gate", entry, navigation],
    queryFn: ({ signal }) => getSetupStatus({ signal }),
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
