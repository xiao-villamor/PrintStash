"use client";

/**
 * Thin toast wrapper over the ``sonner`` library.
 *
 * RULES:
 * - ``toast.error`` for API errors and failures → red, auto-dismiss after 5 s.
 * - ``toast.success`` for confirmations (saved, deleted, sent) → green, 3 s.
 * - ``toast.warning`` for auth-related nudges → amber, 5 s.
 * - ``toast`` (neutral) for one-off info messages.
 *
 * Centralising imports here means every component picks up ``sonner`` from
 * one place; if we later swap the library we only touch this file.
 */

import { toast as sonner } from "sonner";
import { userMessage, parseApiError } from "@/lib/errors";

// ---------------------------------------------------------------------------
// Typed helpers — prefer these over the raw `toast()` function.
// ---------------------------------------------------------------------------

export const toast = {
  /** Show a red error toast with a description parsed from the thrown value. */
  error(raw: unknown): void {
    const msg = userMessage(raw);
    console.debug("PrintStash API error", parseApiError(raw));
    sonner.error(msg, { duration: 6000 });
  },

  /** Show a green success toast. */
  success(message: string): void {
    sonner.success(message, { duration: 3000 });
  },

  /** Show an amber warning toast. */
  warning(message: string, description?: string): void {
    sonner.warning(message, { description, duration: 5000 });
  },

  /** Show a neutral info toast. */
  info(message: string, description?: string): void {
    sonner(message, { description, duration: 4000 });
  },
};

/**
 * Convenience: wraps an async function. On success does nothing; on failure
 * calls `toast.error(e)`.
 *
 * ```ts
 * await toast.catch(deleteModel(id));
 * ```
 */
export async function toastCatch<T>(promise: Promise<T>): Promise<T | undefined> {
  try {
    return await promise;
  } catch (e) {
    toast.error(e);
    return undefined;
  }
}
