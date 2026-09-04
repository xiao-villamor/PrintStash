import { getJson, sendAction, sendJson } from "@/lib/api/request";
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

export function listProviderConnections(): Promise<ProviderConnectionRead[]> {
  return getJson<ProviderConnectionRead[]>(CONNECTIONS_PATH, { fresh: true });
}

export function authorizeMyMiniFactory(): Promise<OAuthAuthorizeRead> {
  return sendJson<OAuthAuthorizeRead>(`${CONNECTIONS_PATH}/myminifactory/authorize`, "POST", {});
}

export function connectCults(body: CultsConnectRequest): Promise<ProviderConnectionRead> {
  return sendJson<ProviderConnectionRead>(`${CONNECTIONS_PATH}/cults/connect`, "POST", body);
}

export function disconnectProvider(provider: CaptureProvider): Promise<void> {
  return sendAction(`${CONNECTIONS_PATH}/${provider}/disconnect`, "DELETE");
}

export function createBrowserPairing(): Promise<BrowserPairingCreateRead> {
  return sendJson<BrowserPairingCreateRead>(PAIRINGS_PATH, "POST", {});
}

export function listBrowserDevices(): Promise<BrowserDeviceRead[]> {
  return getJson<BrowserDeviceRead[]>(PAIRINGS_PATH, { fresh: true });
}

export function renameBrowserDevice(
  deviceId: number,
  body: BrowserDevicePatch,
): Promise<BrowserDeviceRead> {
  return sendJson<BrowserDeviceRead>(`${PAIRINGS_PATH}/${deviceId}`, "PATCH", body);
}

export function revokeBrowserDevice(deviceId: number): Promise<void> {
  return sendAction(`${PAIRINGS_PATH}/${deviceId}`, "DELETE");
}
