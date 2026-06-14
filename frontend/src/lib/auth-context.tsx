"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  getToken,
  getUser,
  isLoggedIn,
  storeLogin,
  clearLogin,
  onAuthChange,
  type StoredUser,
} from "@/lib/auth-store";
import { login as apiLogin, getMe } from "@/lib/api";

interface AuthState {
  user: StoredUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  logout: () => {},
  refresh: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<StoredUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isLoggedIn()) {
      setLoading(false);
      return;
    }
    getMe()
      .then((u) => {
        const stored: StoredUser = {
          id: u.id,
          username: u.username,
          email: u.email,
          is_superuser: u.is_superuser,
        };
        storeLogin(getToken()!, stored, { silent: true });
        setUser(stored);
      })
      .catch(() => {
        clearLogin();
      })
      .finally(() => setLoading(false));
    const off = onAuthChange(() => {
      setUser(getUser());
    });
    return off;
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const token = await apiLogin({ username, password });
    storeLogin(token.access_token, { id: 0, username, email: null, is_superuser: false });
    try {
      const me = await getMe();
      const stored: StoredUser = {
        id: me.id,
        username: me.username,
        email: me.email,
        is_superuser: me.is_superuser,
      };
      storeLogin(token.access_token, stored);
      setUser(stored);
    } catch (e) {
      clearLogin();
      throw e;
    }
  }, []);

  const logout = useCallback(() => {
    clearLogin();
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    if (!isLoggedIn()) return;
    try {
      const me = await getMe();
      const stored: StoredUser = {
        id: me.id,
        username: me.username,
        email: me.email,
        is_superuser: me.is_superuser,
      };
      storeLogin(getToken()!, stored, { silent: true });
      setUser(stored);
    } catch {
      clearLogin();
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
