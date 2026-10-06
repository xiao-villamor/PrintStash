import {
  getJson,
  getUrl,
  sendAction,
  sendJson,
  requestApi,
  jsonHeaders,
  type GetJsonOptions,
} from "@/lib/api/request";
import {
  ApiKeyCreateResponse,
  ApiKeyRead,
  AuthProvidersRead,
  LoginRequest,
  TokenResponse,
  UserCreate,
  UserPasswordUpdate,
  UserRead,
  UserUpdate,
} from "@/types";

export function getAuthProviders(options?: GetJsonOptions): Promise<AuthProvidersRead> {
  return getJson<AuthProvidersRead>("/api/v1/auth/providers", options);
}

export function oidcLoginUrl(): string {
  return getUrl("/api/v1/auth/oidc/login");
}

export function login(
  body: LoginRequest,
  options?: { signal?: AbortSignal },
): Promise<TokenResponse> {
  return requestApi<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options?.signal,
  });
}

export function logout(options?: { signal?: AbortSignal }): Promise<void> {
  return requestApi<void>("/api/v1/auth/logout", {
    method: "POST",
    headers: jsonHeaders(),
    signal: options?.signal,
  });
}

export function getMe(options?: GetJsonOptions): Promise<UserRead> {
  return getJson<UserRead>("/api/v1/auth/me", options);
}

export function listApiKeys(): Promise<ApiKeyRead[]> {
  return getJson<ApiKeyRead[]>("/api/v1/auth/api-keys");
}

export function listAdminUsers(): Promise<UserRead[]> {
  return getJson<UserRead[]>("/api/v1/admin/users", { fresh: true });
}

export function createAdminUser(payload: UserCreate): Promise<UserRead> {
  return sendJson<UserRead>("/api/v1/admin/users", "POST", payload);
}

export function updateAdminUser(id: number, payload: UserUpdate): Promise<UserRead> {
  return sendJson<UserRead>(`/api/v1/admin/users/${id}`, "PATCH", payload);
}

export function resetAdminUserPassword(id: number, payload: UserPasswordUpdate): Promise<UserRead> {
  return sendJson<UserRead>(`/api/v1/admin/users/${id}/password`, "POST", payload);
}

export function deactivateAdminUser(id: number): Promise<void> {
  return sendAction(`/api/v1/admin/users/${id}`, "DELETE");
}

export function createApiKey(name: string): Promise<ApiKeyCreateResponse> {
  return sendJson<ApiKeyCreateResponse>("/api/v1/auth/api-keys", "POST", { name });
}

export function revokeApiKey(id: number): Promise<void> {
  return sendAction(`/api/v1/auth/api-keys/${id}`, "DELETE");
}
