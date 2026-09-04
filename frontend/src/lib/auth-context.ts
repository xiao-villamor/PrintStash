"use client";

import { createContext, useContext } from "react";
import { type StoredUser } from "@/lib/auth-store";
import { login as apiLogin, logout as apiLogout, getMe } from "@/lib/api";

/**
 * The auth calls the provider makes. Declared as a port so a test (or an
 * embedded surface) can stand the provider up against a stub; production
 * callers get {@link SERVER_AUTH_API}.
 */
export interface AuthApi {
  login: typeof apiLogin;
  logout: typeof apiLogout;
  getMe: typeof getMe;
}

export interface AuthState {
  user: StoredUser | null;
  loading: boolean;
  login: (username: string, password: string, remember_me?: boolean) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

/**
 * Exported so a test (or a future embedded surface) can supply an auth state
 * directly instead of standing the whole provider up against the API.
 */
export const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
