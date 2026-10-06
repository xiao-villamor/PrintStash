"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
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
import {
  getSessionVersion,
  requireSessionVersion,
  withSessionRequest,
  type SessionRequest,
} from "@/lib/session-transport";
import { markStartup } from "@/lib/startup-timing";

const SERVER_AUTH_API: AuthApi = { login: apiLogin, logout: apiLogout, getMe };

export function AuthProvider({
  children,
  api = SERVER_AUTH_API,
  boundary = "private",
}: {
  children: React.ReactNode;
  api?: AuthApi;
  boundary?: "private" | "entry";
}) {
  const version = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [user, setUser] = useState<StoredUser | null>(null);
  // Only a stored login has a session worth confirming with `getMe`; with no
  // stored login there is nothing to await, so the provider is ready at once.
  const [loading, setLoading] = useState(isLoggedIn);

  const epoch = useRef(0);
  const active = useRef(new Set<AbortController>());
  const run = useCallback(
    async <T,>(operation: (request: SessionRequest) => Promise<T>, signal?: AbortSignal) => {
      const controller = new AbortController();
      const cancel = () => controller.abort(signal?.reason);
      if (signal?.aborted) cancel();
      else signal?.addEventListener("abort", cancel, { once: true });
      active.current.add(controller);
      try {
        return await withSessionRequest(operation, controller.signal);
      } finally {
        active.current.delete(controller);
        signal?.removeEventListener("abort", cancel);
      }
    },
    [],
  );

  useEffect(() => {
    epoch.current++;
    const dispose = () => {
      epoch.current++;
      for (const controller of active.current)
        controller.abort(new DOMException("auth_provider_disposed", "AbortError"));
    };
    let alive = true;
    let checking = true;
    const off = onAuthChange(() => {
      checking = false;
      setLoading(false);
      setUser(getUser());
    });

    if (!isLoggedIn())
      return () => {
        off();
        dispose();
      };

    run((request) => api.getMe({ signal: request.signal }))
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
      dispose();
    };
  }, [api, run]);

  const login = useCallback(
    async (
      username: string,
      password: string,
      remember_me: boolean = false,
      signal?: AbortSignal,
    ) => {
      if (signal?.aborted) throw signal.reason;
      clearLogin();
      setUser(null);
      const version = getSessionVersion();
      const started = epoch.current;
      try {
        const { token, me } = await run(async (request) => {
          const token = await api.login(
            { username, password, remember_me },
            { signal: request.signal },
          );
          request.assertCurrent();
          const me = await api.getMe({ signal: request.signal });
          request.assertCurrent();
          return { token, me };
        }, signal);
        requireSessionVersion(version);
        if (signal?.aborted) throw signal.reason;
        if (started !== epoch.current)
          throw new DOMException("auth_provider_disposed", "AbortError");
        const stored: StoredUser = {
          id: me.id,
          username: me.username,
          email: me.email,
          is_superuser: me.is_superuser,
        };
        // This verified publication intentionally starts the next session incarnation.
        storeLogin(token.access_token, stored);
        setUser(stored);
      } catch (error) {
        if (
          version === getSessionVersion() &&
          !signal?.aborted &&
          started === epoch.current &&
          !(error instanceof Error && error.name === "AbortError")
        )
          clearLogin();
        throw error;
      }
    },
    [api, run],
  );

  const logout = useCallback(async () => {
    clearLogin();
    setUser(null);
    await run((request) => api.logout({ signal: request.signal }));
  }, [api, run]);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const version = getSessionVersion();
      const started = epoch.current;
      try {
        const me = await run((request) => api.getMe({ signal: request.signal }), signal);
        requireSessionVersion(version);
        if (signal?.aborted) throw signal.reason;
        if (started !== epoch.current)
          throw new DOMException("auth_provider_disposed", "AbortError");
        const stored: StoredUser = {
          id: me.id,
          username: me.username,
          email: me.email,
          is_superuser: me.is_superuser,
        };
        storeLogin("", stored, { silent: true });
        setUser(stored);
      } catch (error) {
        if (
          version === getSessionVersion() &&
          !signal?.aborted &&
          started === epoch.current &&
          !(error instanceof Error && error.name === "AbortError")
        ) {
          clearLogin();
          setUser(null);
        }
        throw error;
      }
    },
    [api, run],
  );

  return (
    <AuthContext.Provider
      key={boundary === "private" ? `private:${version}` : "entry"}
      value={{ user, loading, login, logout, refresh }}
    >
      {children}
    </AuthContext.Provider>
  );
}
