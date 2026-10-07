/** Immediate configuration preferences own one pending intent and explicit conflict review. */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { captureEditingBase } from "@/lib/api/editing";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useVaultConfigCommand, vaultConfigOptions } from "./settings-config";
import type { EditingBase } from "@/types/editing";
import type { VaultConfigRead, VaultConfigUpdate } from "@/types";

export type PreferenceIntent =
  | { kind: "auto-mark"; value: boolean }
  | { kind: "currency"; value: string }
  | { kind: "thumbnail-width"; value: NonNullable<VaultConfigUpdate["model_thumbnail_width"]> };
type PendingPreference = { intent: PreferenceIntent; base: EditingBase };
type PreferenceReview =
  | { phase: "idle" }
  | ({ phase: "saving" } & PendingPreference)
  | ({ phase: "required" | "loading"; problem: "conflict" | "unconfirmed" } & PendingPreference)
  | ({
      phase: "ready";
      problem: "conflict" | "unconfirmed";
      snapshot: VaultConfigRead;
    } & PendingPreference);

function payload(intent: PreferenceIntent): VaultConfigUpdate {
  switch (intent.kind) {
    case "auto-mark":
      return { auto_mark_known_good: intent.value };
    case "currency":
      return { currency: intent.value };
    case "thumbnail-width":
      return { model_thumbnail_width: intent.value };
  }
}
export function savedPreference(snapshot: VaultConfigRead, intent: PreferenceIntent) {
  switch (intent.kind) {
    case "auto-mark":
      return snapshot.auto_mark_known_good;
    case "currency":
      return snapshot.currency;
    case "thumbnail-width":
      return snapshot.model_thumbnail_width;
  }
}
export function useSettingsPreferenceCommand() {
  const client = useQueryClient();
  const command = useVaultConfigCommand();
  const [session] = useState(getSessionVersion);
  const [state, setState] = useState<PreferenceReview>({ phase: "idle" });
  const live = useRef(true);
  const current = () => live.current && session === getSessionVersion();
  function assertCurrent() {
    requireSessionVersion(session);
    if (!live.current) throw new DOMException("Preference view was disposed", "AbortError");
  }
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => setState({ phase: "idle" }));
    return () => {
      live.current = false;
      release();
    };
  }, []);
  async function submit(intent: PreferenceIntent, base: EditingBase) {
    assertCurrent();
    if (command.isPending) throw new Error("A preference command is already pending");
    const captured = captureEditingBase(base);
    setState({ phase: "saving", intent, base: captured });
    try {
      const receipt = await command.mutateAsync({
        session,
        payload: payload(intent),
        base: captured,
      });
      assertCurrent();
      setState({ phase: "idle" });
      return receipt;
    } catch (error) {
      if (current()) {
        const status = parseApiError(error).status;
        if (status === 412 || status === 428 || status === 0 || status >= 500)
          setState({
            phase: "required",
            intent,
            base: captured,
            problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
          });
        else setState({ phase: "idle" });
      }
      throw error;
    }
  }
  async function review() {
    assertCurrent();
    if (state.phase !== "required" && state.phase !== "ready") return;
    const pending = { intent: state.intent, base: state.base, problem: state.problem };
    setState({ phase: "loading", ...pending });
    try {
      await client.cancelQueries({ queryKey: queryKeys.vaultConfig, exact: true });
      assertCurrent();
      const snapshot = await client.fetchQuery({
        ...vaultConfigOptions(),
        staleTime: 0,
        retry: false,
      });
      assertCurrent();
      setState({ phase: "ready", ...pending, snapshot });
    } catch (error) {
      if (current()) setState({ phase: "required", ...pending });
      throw error;
    }
  }
  return {
    state,
    intent: state.phase === "idle" ? null : state.intent,
    blocked: state.phase !== "idle",
    async run(intent: PreferenceIntent, base: EditingBase) {
      if (state.phase !== "idle") throw new Error("Review the pending preference first");
      return submit(intent, base);
    },
    review,
    async retry() {
      if (state.phase !== "ready") throw new Error("Preference review is required");
      return submit(state.intent, state.snapshot);
    },
    adopt() {
      assertCurrent();
      if (state.phase === "ready") setState({ phase: "idle" });
    },
  };
}
