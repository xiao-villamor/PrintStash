import { getJson, getUrl, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
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

export function listApiKeys(options: GetJsonOptions = {}): Promise<ApiKeyRead[]> {
  return getJson<ApiKeyRead[]>("/api/v1/auth/api-keys", { ...options });
}

export function listAdminUsers(options: GetJsonOptions = {}): Promise<UserRead[]> {
  return getJson<UserRead[]>("/api/v1/admin/users", { ...options });
}

export function createAdminUser(payload: UserCreate): Promise<UserRead> {
  return requestApi<UserRead>("/api/v1/admin/users", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
}

export function updateAdminUser(id: number, payload: UserUpdate): Promise<UserRead> {
  return requestApi<UserRead>(`/api/v1/admin/users/${id}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
}

export function resetAdminUserPassword(id: number, payload: UserPasswordUpdate): Promise<UserRead> {
  return requestApi<UserRead>(`/api/v1/admin/users/${id}/password`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
}

export function deactivateAdminUser(id: number): Promise<void> {
  return requestApi<void>(`/api/v1/admin/users/${id}`, { method: "DELETE" });
}

export function createApiKey(name: string): Promise<ApiKeyCreateResponse> {
  return requestApi<ApiKeyCreateResponse>("/api/v1/auth/api-keys", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ name }),
  });
}

export function revokeApiKey(id: number): Promise<void> {
  return requestApi<void>(`/api/v1/auth/api-keys/${id}`, { method: "DELETE" });
}
