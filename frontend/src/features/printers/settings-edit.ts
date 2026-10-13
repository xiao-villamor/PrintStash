/** Printer settings commands own conditional writes and authorized conflict review.
 * Credential drafts remain in the form, outside the Query and Mutation caches.
 */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { updatePrinter } from "@/lib/api/printers";
import { captureEditingBase } from "@/lib/api/editing";
import { parseApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { PrinterRead, PrinterUpdate } from "@/types";
import { printerDetailOptions, printerKeys } from "./queries";

type ReviewProblem = "conflict" | "unconfirmed";
export type PrinterEditState =
  | { phase: "idle" }
  | { phase: "saving" }
  | { phase: "required" | "loading"; problem: ReviewProblem }
  | { phase: "ready"; problem: ReviewProblem; snapshot: PrinterRead };

export function usePrinterSettingsCommand(id: number) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const [state, setState] = useState<PrinterEditState>({ phase: "idle" });
  const live = useRef(true);
  const pending = useRef(false);
  const current = () => live.current && session === getSessionVersion();
  function assertCurrent() {
    requireSessionVersion(session);
    if (!live.current) throw new DOMException("Printer editor was disposed", "AbortError");
  }
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  async function review() {
    assertCurrent();
    if (pending.current || (state.phase !== "required" && state.phase !== "ready")) return;
    const problem = state.problem;
    pending.current = true;
    setState({ phase: "loading", problem });
    try {
      await client.cancelQueries({ queryKey: printerKeys.detail(id), exact: true });
      assertCurrent();
      const snapshot = await client.fetchQuery({
        ...printerDetailOptions(id),
        staleTime: 0,
        retry: false,
      });
      assertCurrent();
      if (!snapshot.access.can_admin) throw new Error("printer_permission_denied");
      captureEditingBase(snapshot);
      setState({ phase: "ready", problem, snapshot });
    } catch (error) {
      if (current()) setState({ phase: "required", problem });
      throw error;
    } finally {
      pending.current = false;
    }
  }
  async function save(snapshot: PrinterRead, payload: PrinterUpdate, revised = false) {
    assertCurrent();
    if (pending.current) throw new Error("A printer edit is already pending");
    if (revised ? state.phase !== "ready" : state.phase !== "idle")
      throw new Error("Review the pending printer edit first");
    const target = revised && state.phase === "ready" ? state.snapshot : snapshot;
    if (target.id !== id || snapshot.id !== id) throw new Error("printer_identity_mismatch");
    if (
      target.provider !== snapshot.provider ||
      target.provider_variant !== snapshot.provider_variant ||
      target.prusalink_auth_mode !== snapshot.prusalink_auth_mode
    )
      throw new Error("Adopt the current connection before editing its settings");
    const base = captureEditingBase(target);
    pending.current = true;
    setState({ phase: "saving" });
    try {
      const saved = await updatePrinter(id, payload, { base });
      assertCurrent();
      await client.cancelQueries({ queryKey: printerKeys.detail(id), exact: true });
      assertCurrent();
      client.setQueryData<PrinterRead>(printerKeys.detail(id), (cached) =>
        cached &&
        (cached.edit_epoch !== saved.edit_epoch || cached.edit_version > saved.edit_version)
          ? cached
          : saved,
      );
      void client.invalidateQueries({
        queryKey: printerKeys.all,
        predicate: (query) =>
          query.queryKey.length === 1 ||
          query.queryKey[1] === "dashboard" ||
          (query.queryKey[1] === id && query.queryKey.length > 2),
      });
      setState({ phase: "idle" });
      return saved;
    } catch (error) {
      if (current()) {
        const status = parseApiError(error).status;
        setState(
          status === 412 || status === 428 || status === 0 || status >= 500
            ? {
                phase: "required",
                problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
              }
            : { phase: "idle" },
        );
      }
      throw error;
    } finally {
      pending.current = false;
    }
  }
  function adopt() {
    assertCurrent();
    if (state.phase !== "ready") throw new Error("Review current printer settings first");
    const snapshot = state.snapshot;
    setState({ phase: "idle" });
    return snapshot;
  }
  return {
    state,
    review,
    save,
    adopt,
    blocked: state.phase !== "idle",
    busy: state.phase === "saving" || state.phase === "loading",
  };
}
