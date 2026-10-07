import { editHeaders, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";
import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import type {
  BrowserDevicePatch,
  BrowserDeviceRead,
  BrowserPairingCreateRead,
  CaptureProvider,
  CultsConnectRequest,
  OAuthAuthorizeRead,
  ProviderConnectionRead,
} from "@/types";

const CONNECTIONS_PATH = "/api/v1/provider-connections";
const PAIRINGS_PATH = "/api/v1/browser-pairings";

export function listProviderConnections(
  options: GetJsonOptions = {},
): Promise<ProviderConnectionRead[]> {
  return getJson<ProviderConnectionRead[]>(CONNECTIONS_PATH, { ...options });
}

export function authorizeMyMiniFactory(
  options: { signal?: AbortSignal } = {},
): Promise<OAuthAuthorizeRead> {
  return requestApi<OAuthAuthorizeRead>(`${CONNECTIONS_PATH}/myminifactory/authorize`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function connectCults(
  body: CultsConnectRequest,
  options: { signal?: AbortSignal } = {},
): Promise<ProviderConnectionRead> {
  return requestApi<ProviderConnectionRead>(`${CONNECTIONS_PATH}/cults/connect`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function disconnectProvider(
  provider: CaptureProvider,
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  return requestApi<void>(`${CONNECTIONS_PATH}/${provider}/disconnect`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export function createBrowserPairing(
  options: { signal?: AbortSignal } = {},
): Promise<BrowserPairingCreateRead> {
  return requestApi<BrowserPairingCreateRead>(PAIRINGS_PATH, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function listBrowserDevices(options: GetJsonOptions = {}): Promise<BrowserDeviceRead[]> {
  return getJson<BrowserDeviceRead[]>(PAIRINGS_PATH, { ...options });
}

export async function renameBrowserDevice(
  deviceId: number,
  body: BrowserDevicePatch,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<BrowserDeviceRead> {
  const saved = await requestApi<BrowserDeviceRead>(`${PAIRINGS_PATH}/${deviceId}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...editHeaders("browser-device", deviceId, options.base) },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  if (saved.id !== deviceId) throw new Error("browser_identity_mismatch");
  return saved;
}

export function revokeBrowserDevice(
  deviceId: number,
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  return requestApi<void>(`${PAIRINGS_PATH}/${deviceId}`, {
    method: "DELETE",
    signal: options.signal,
  });
}
