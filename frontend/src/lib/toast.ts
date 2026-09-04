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

/**
 * Is this a sentence the app wrote, rather than something it caught?
 *
 * `toast.error` takes both: a thrown `ApiError`, and — at a dozen call sites —
 * a literal validation message ("Enter a temperature between 0 and 500."). Those
 * two need opposite handling, and `parseApiError` cannot tell them apart: free
 * text matches no HTTP envelope and no detail code, so it collapses to `unknown`
 * and the user is shown "Something went wrong reaching the server" for a typo in
 * a form field.
 *
 * A detail code is a single snake_case token by construction (`model_not_found`,
 * and the bare codes background jobs report), so whitespace is what separates
 * the two. Anything with a space in it was written to be read.
 */
function isAuthoredMessage(cause: unknown): cause is string {
  // oxlint-disable-next-line anti-slop/no-runtime-typeof -- `cause` is genuinely untyped: this is the boundary where a thrown value is classified, and a string is one of the shapes callers pass.
  return typeof cause === "string" && /\s/.test(cause);
}

export const toast = {
  /** Show a red error toast with a description parsed from the thrown value. */
  error(cause: unknown): void {
    if (isAuthoredMessage(cause)) {
      sonner.error(cause, { duration: 6000 });
      return;
    }
    const msg = userMessage(cause);
    console.debug("PrintStash API error", parseApiError(cause));
    sonner.error(msg, { duration: 6000 });
  },

  /** Show a green success toast. */
  success(message: string): void {
    sonner.success(message, { duration: 3000 });
  },

  undo(message: string, onUndo: () => void | Promise<void>): void {
    sonner.success(message, {
      duration: 6000,
      action: {
        label: "Undo",
        onClick: () => {
          void onUndo();
        },
      },
    });
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
