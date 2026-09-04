export type CaptureProvider = "myminifactory" | "cults";

/** A connection status deliberately excludes any provider credential. */
export interface ProviderConnectionRead {
  provider: CaptureProvider;
  connected: boolean;
  updated_at: string | null;
}

export interface OAuthAuthorizeRead {
  authorization_url: string;
}

export interface CultsConnectRequest {
  username: string;
  password: string;
}

/** A one-time pairing code. Keep it in memory only; it is never a device credential. */
export interface BrowserPairingCreateRead {
  code: string;
  expires_at: string;
}

/** A paired browser's public record. Device credentials are never returned here. */
export interface BrowserDeviceRead {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface BrowserDevicePatch {
  name: string;
}
