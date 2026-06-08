import { authHeaders, getUrl, handleResponse, sendAction, sendJson } from "@/lib/api/request";
import { ApiKeyCreateResponse, ApiKeyRead, LoginRequest, TokenResponse, UserRead } from "@/types";

export function login(body: LoginRequest): Promise<TokenResponse> {
  return sendJson<TokenResponse>("/api/v1/auth/login", "POST", body);
}

export async function getMe(): Promise<UserRead> {
  const res = await fetch(getUrl("/api/v1/auth/me"), {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handleResponse<UserRead>(res);
}

export async function listApiKeys(): Promise<ApiKeyRead[]> {
  const res = await fetch(getUrl("/api/v1/auth/api-keys"), {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handleResponse<ApiKeyRead[]>(res);
}

export function createApiKey(name: string): Promise<ApiKeyCreateResponse> {
  return sendJson<ApiKeyCreateResponse>("/api/v1/auth/api-keys", "POST", { name });
}

export function revokeApiKey(id: number): Promise<void> {
  return sendAction(`/api/v1/auth/api-keys/${id}`, "DELETE");
}
