"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
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
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { markStartup } from "@/lib/startup-timing";

const SERVER_AUTH_API: AuthApi = { login: apiLogin, logout: apiLogout, getMe };

export function AuthProvider({
  children,
  api = SERVER_AUTH_API,
}: {
  children: React.ReactNode;
  api?: AuthApi;
}) {
  const version = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [user, setUser] = useState<StoredUser | null>(null);
  // Only a stored login has a session worth confirming with `getMe`; with no
  // stored login there is nothing to await, so the provider is ready at once.
  const [loading, setLoading] = useState(isLoggedIn);

  useEffect(() => {
    let alive = true;
    let checking = true;
    const off = onAuthChange(() => {
      checking = false;
      setLoading(false);
      setUser(getUser());
    });

    if (!isLoggedIn()) return off;

    api
      .getMe()
      .then((u) => {
        if (!alive || !checking) return;
        const stored: StoredUser = {
          id: u.id,
          username: u.username,
          email: u.email,
          is_superuser: u.is_superuser,
        };
        checking = false;
        storeLogin("", stored, { silent: true });
        markStartup("session-validated");
        setUser(stored);
      })
      .catch(() => {
        if (!alive || !checking) return;
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
      clearLogin();
      setUser(null);
      const version = getSessionVersion();
      const token = await api.login({ username, password, remember_me });
      requireSessionVersion(version);
      try {
        const me = await api.getMe();
        requireSessionVersion(version);
        const stored: StoredUser = {
          id: me.id,
          username: me.username,
          email: me.email,
          is_superuser: me.is_superuser,
        };
        storeLogin(token.access_token, stored);
        setUser(stored);
      } catch (e) {
        if (version === getSessionVersion()) clearLogin();
        throw e;
      }
    },
    [api],
  );

  const logout = useCallback(async () => {
    clearLogin();
    setUser(null);
    await api.logout();
  }, [api]);

  const refresh = useCallback(async () => {
    const version = getSessionVersion();
    try {
      const me = await api.getMe();
      requireSessionVersion(version);
      const stored: StoredUser = {
        id: me.id,
        username: me.username,
        email: me.email,
        is_superuser: me.is_superuser,
      };
      storeLogin("", stored, { silent: true });
      setUser(stored);
    } catch (error) {
      if (version === getSessionVersion()) {
        clearLogin();
        setUser(null);
      }
      throw error;
    }
  }, [api]);

  return (
    <AuthContext.Provider key={version} value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}
