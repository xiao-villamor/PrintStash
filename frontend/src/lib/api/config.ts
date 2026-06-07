import { getJson, sendJson } from "@/lib/api/request";
import {
  SetupRequest,
  SetupResponse,
  SetupStatus,
  VaultConfigRead,
  VaultConfigUpdate,
} from "@/types";

export function getSetupStatus(): Promise<SetupStatus> {
  return getJson<SetupStatus>("/api/v1/setup/status");
}

export function completeSetup(body: SetupRequest): Promise<SetupResponse> {
  return sendJson<SetupResponse>("/api/v1/setup", "POST", body);
}

export function getVaultConfig(): Promise<VaultConfigRead> {
  return getJson<VaultConfigRead>("/api/v1/config");
}

export function updateVaultConfig(body: VaultConfigUpdate): Promise<VaultConfigRead> {
  return sendJson<VaultConfigRead>("/api/v1/config", "PUT", body);
}
