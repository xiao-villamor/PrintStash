"use client";

/**
 * Auth-awareness helpers for write-operation components.
 *
 * ``isAuthenticated()`` — true when the user has a JWT token.
 *
 * ``guardWrite(op)`` — wraps an async operation: if not authenticated, shows
 * an amber toast explaining how to proceed; if authenticated, runs ``op``.
 *
 * ``blockReason`` — returns ``null`` if the user can write, or a short string
 * (e.g. "Sign in required") suitable for a disabled-button label.
 */

import { useCallback, useMemo } from "react";

import { isLoggedIn } from "@/lib/auth";
import { toast } from "@/lib/toast";

export interface UseRequireAuthReturn {
  /** True if the user holds a JWT. */
  isAuthenticated: boolean;
  /** Human-readable reason a write operation is blocked, or null. */
  blockReason: string | null;
  /**
   * Run ``op()`` only if authenticated; otherwise toast a warning.
   * Returns the operation's result or ``undefined`` on auth gate / error.
   */
  guardWrite<T>(op: () => Promise<T>): Promise<T | undefined>;
  /** Show a toast telling the user to sign in. */
  showAuthRequiredToast(): void;
  /** Show a toast telling the user their session expired. */
  showSessionExpiredToast(): void;
}

const AUTH_REQUIRED_MSG =
  "Sign in required to perform this action. Use the login page to continue.";

export function useRequireAuth(): UseRequireAuthReturn {
  const isAuthenticated = useMemo(() => isLoggedIn(), []);

  const blockReason = useMemo(
    () => (isAuthenticated ? null : "Sign in required"),
    [isAuthenticated],
  );

  const showAuthRequiredToast = useCallback(() => {
    toast.warning("Authentication required", AUTH_REQUIRED_MSG);
  }, []);

  const showSessionExpiredToast = useCallback(() => {
    toast.warning("Session expired", "Please sign in again to continue.");
  }, []);

  const guardWrite = useCallback(
    async <T>(op: () => Promise<T>): Promise<T | undefined> => {
      if (!isAuthenticated) {
        showAuthRequiredToast();
        return undefined;
      }
      return op();
    },
    [isAuthenticated, showAuthRequiredToast],
  );

  return {
    isAuthenticated,
    blockReason,
    guardWrite,
    showAuthRequiredToast,
    showSessionExpiredToast,
  };
}
