/** User-scoped provider/device reads; credentials and pairing receipts are never cached. */
import { useEffect, useRef } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import * as providerApi from "@/lib/api/provider-connections";
import { parseApiError } from "@/lib/errors";
import { onAuthChange } from "@/lib/auth-store";
import { requireSessionVersion } from "@/lib/session-transport";
import type {
  BrowserDeviceRead,
  CaptureProvider,
  CultsConnectRequest,
  ProviderConnectionRead,
} from "@/types";

export type ProviderConnectionsTransport = typeof providerApi;
export const providerConnectionKeys = {
  all: ["provider-accounts"] as const,
  connections: ["provider-accounts", "connections"] as const,
  devices: ["provider-accounts", "devices"] as const,
};
export function providerConnectionsOptions(api: ProviderConnectionsTransport = providerApi) {
  return queryOptions({
    queryKey: providerConnectionKeys.connections,
    queryFn: ({ signal }) => api.listProviderConnections({ signal }),
    retry: false,
  });
}
export function browserDevicesOptions(api: ProviderConnectionsTransport = providerApi) {
  return queryOptions({
    queryKey: providerConnectionKeys.devices,
    queryFn: ({ signal }) => api.listBrowserDevices({ signal }),
    retry: false,
  });
}

export function useProviderConnectionCommands(api: ProviderConnectionsTransport = providerApi) {
  const client = useQueryClient();
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      active.current = null;
    });
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, []);

  // This private lifetime guard keeps secret-bearing arguments out of mutation
  // history and prevents a disposed OAuth command from navigating another page.
  async function run<T>(
    session: number,
    operation: (signal: AbortSignal, current: () => void) => Promise<T>,
  ): Promise<T> {
    requireSessionVersion(session);
    if (!live.current) throw new DOMException("Provider panel disposed", "AbortError");
    if (active.current) throw new Error("Provider command already pending");
    const request = new AbortController();
    active.current = request;
    const current = () => {
      requireSessionVersion(session);
      request.signal.throwIfAborted();
    };
    try {
      await client.cancelQueries({ queryKey: providerConnectionKeys.all });
      current();
      const result = await operation(request.signal, current);
      current();
      return result;
    } catch (error) {
      current();
      if ([401, 403, 404].includes(parseApiError(error).status))
        await client.invalidateQueries({ queryKey: providerConnectionKeys.all });
      throw error;
    } finally {
      if (active.current === request) active.current = null;
    }
  }
  return {
    authorize: (session: number) =>
      run(session, (signal) => api.authorizeMyMiniFactory({ signal })),
    pair: (session: number) => run(session, (signal) => api.createBrowserPairing({ signal })),
    connect: (body: CultsConnectRequest, session: number) =>
      run(session, async (signal, current) => {
        const result = await api.connectCults(body, { signal });
        current();
        await client.cancelQueries({ queryKey: providerConnectionKeys.connections });
        current();
        client.setQueryData<ProviderConnectionRead[]>(
          providerConnectionKeys.connections,
          (rows) => [...(rows ?? []).filter((row) => row.provider !== result.provider), result],
        );
        return result;
      }),
    rename: (id: number, name: string, session: number) =>
      run(session, async (signal, current) => {
        const result = await api.renameBrowserDevice(id, { name }, { signal });
        current();
        await client.cancelQueries({ queryKey: providerConnectionKeys.devices });
        current();
        client.setQueryData<BrowserDeviceRead[]>(providerConnectionKeys.devices, (rows) =>
          rows?.map((row) => (row.id === result.id ? result : row)),
        );
        return result;
      }),
    disconnect: (provider: CaptureProvider, session: number) =>
      run(session, async (signal, current) => {
        await api.disconnectProvider(provider, { signal });
        current();
        await client.invalidateQueries({ queryKey: providerConnectionKeys.connections });
      }),
    revoke: (id: number, session: number) =>
      run(session, async (signal, current) => {
        await api.revokeBrowserDevice(id, { signal });
        current();
        await client.invalidateQueries({ queryKey: providerConnectionKeys.devices });
      }),
  };
}
