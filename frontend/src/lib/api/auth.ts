import { authHeaders, getUrl, handleResponse, sendJson } from "@/lib/api/request";
import { LoginRequest, TokenResponse, UserRead } from "@/types";

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
