"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getUser,
  isLoggedIn,
  storeLogin,
  clearLogin,
  onAuthChange,
  type StoredUser,
} from "@/lib/auth-store";
import { login as apiLogin, logout as apiLogout, getMe } from "@/lib/api";
import { AuthContext, type AuthApi } from "@/lib/auth-context";

const SERVER_AUTH_API: AuthApi = { login: apiLogin, logout: apiLogout, getMe };

export function AuthProvider({
  children,
  api = SERVER_AUTH_API,
}: {
  children: React.ReactNode;
  api?: AuthApi;
}) {
  const [user, setUser] = useState<StoredUser | null>(null);
  // Only a stored login has a session worth confirming with `getMe`; with no
  // stored login there is nothing to await, so the provider is ready at once.
  const [loading, setLoading] = useState(isLoggedIn);

  useEffect(() => {
    let alive = true;
    const off = onAuthChange(() => {
      setUser(getUser());
    });

    if (!isLoggedIn()) return off;

    api
      .getMe()
      .then((u) => {
        if (!alive) return;
        const stored: StoredUser = {
          id: u.id,
          username: u.username,
          email: u.email,
          is_superuser: u.is_superuser,
        };
        storeLogin("", stored, { silent: true });
        setUser(stored);
      })
      .catch(() => {
        if (!alive) return;
        clearLogin();
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
      off();
    };
  }, [api]);

  const login = useCallback(
    async (username: string, password: string, remember_me: boolean = false) => {
      const token = await api.login({ username, password, remember_me });
      storeLogin(token.access_token, { id: 0, username, email: null, is_superuser: false });
      try {
        const me = await api.getMe();
        const stored: StoredUser = {
          id: me.id,
          username: me.username,
          email: me.email,
          is_superuser: me.is_superuser,
        };
        storeLogin(token.access_token, stored, { silent: true });
        setUser(stored);
      } catch (e) {
        clearLogin();
        throw e;
      }
    },
    [api],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      clearLogin();
      setUser(null);
    }
  }, [api]);

  const refresh = useCallback(async () => {
    try {
      const me = await api.getMe();
      const stored: StoredUser = {
        id: me.id,
        username: me.username,
        email: me.email,
        is_superuser: me.is_superuser,
      };
      storeLogin("", stored, { silent: true });
      setUser(stored);
    } catch (error) {
      clearLogin();
      setUser(null);
      throw error;
    }
  }, [api]);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}
