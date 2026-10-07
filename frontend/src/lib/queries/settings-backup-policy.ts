/** Backup schedule/local-destination edits keep one coherent config base through explicit review. */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { captureEditingBase } from "@/lib/api/editing";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useVaultConfigCommand, vaultConfigOptions } from "./settings-config";
import type { EditingBase } from "@/types/editing";
import type { VaultConfigRead } from "@/types";

type Policy = Pick<
  VaultConfigRead,
  | "automatic_backups_enabled"
  | "automatic_backup_time_utc"
  | "manual_local_backup_enabled"
  | "automatic_local_backup_enabled"
>;
type Draft = { base: EditingBase; snapshot: Policy; changes: Partial<Policy> };
type Review =
  | { phase: "idle" }
  | { phase: "required" | "loading"; problem: "conflict" | "unconfirmed" }
  | { phase: "ready"; problem: "conflict" | "unconfirmed"; snapshot: VaultConfigRead };
function policy(row: VaultConfigRead): Policy {
  return {
    automatic_backups_enabled: row.automatic_backups_enabled,
    automatic_backup_time_utc: row.automatic_backup_time_utc,
    manual_local_backup_enabled: row.manual_local_backup_enabled,
    automatic_local_backup_enabled: row.automatic_local_backup_enabled,
  };
}
export function useBackupPolicyDraft(config: VaultConfigRead | undefined) {
  const client = useQueryClient();
  const command = useVaultConfigCommand();
  const [session] = useState(getSessionVersion);
  const live = useRef(true);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [review, setReview] = useState<Review>({ phase: "idle" });
  const current = () => live.current && session === getSessionVersion();
  function assertCurrent() {
    requireSessionVersion(session);
    if (!live.current) throw new DOMException("Backup policy view was disposed", "AbortError");
  }
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      setDraft(null);
      setReview({ phase: "idle" });
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  function initial(): Draft {
    assertCurrent();
    if (!config) throw new Error("Backup configuration is unavailable");
    return { base: captureEditingBase(config), snapshot: policy(config), changes: {} };
  }
  function begin() {
    const captured = initial();
    setDraft((previous) => previous ?? captured);
  }
  function edit<K extends keyof Policy>(field: K, value: Policy[K]) {
    const captured = initial();
    setDraft((previous) => {
      const source = previous ?? captured;
      return { ...source, changes: { ...source.changes, [field]: value } };
    });
  }
  function candidate(revised = false): Policy {
    const source = draft ?? initial();
    if (revised) {
      if (review.phase !== "ready") throw new Error("Backup policy review is required");
      return { ...policy(review.snapshot), ...source.changes };
    }
    return { ...source.snapshot, ...source.changes };
  }
  async function save(revised = false) {
    assertCurrent();
    if (command.isPending) throw new Error("A backup policy command is already pending");
    if (!revised && review.phase !== "idle") throw new Error("Backup policy review is required");
    const sent = draft ?? initial();
    const payload = candidate(revised);
    const base =
      revised && review.phase === "ready" ? captureEditingBase(review.snapshot) : sent.base;
    setDraft(sent);
    try {
      const receipt = await command.mutateAsync({ session, base, payload });
      assertCurrent();
      setDraft((latest) =>
        latest === sent
          ? null
          : latest
            ? { ...latest, base: captureEditingBase(receipt), snapshot: policy(receipt) }
            : null,
      );
      setReview({ phase: "idle" });
      return receipt;
    } catch (error) {
      const status = parseApiError(error).status;
      if (current() && (status === 412 || status === 428 || status === 0 || status >= 500))
        setReview({
          phase: "required",
          problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
        });
      throw error;
    }
  }
  async function reviewLatest() {
    assertCurrent();
    if (review.phase !== "required" && review.phase !== "ready") return;
    const problem = review.problem;
    setReview({ phase: "loading", problem });
    try {
      await client.cancelQueries({ queryKey: queryKeys.vaultConfig, exact: true });
      assertCurrent();
      const snapshot = await client.fetchQuery({
        ...vaultConfigOptions(),
        staleTime: 0,
        retry: false,
      });
      assertCurrent();
      setReview({ phase: "ready", problem, snapshot });
    } catch (error) {
      if (current()) setReview({ phase: "required", problem });
      throw error;
    }
  }
  function adopt() {
    assertCurrent();
    if (review.phase !== "ready") throw new Error("Backup policy review is required");
    setDraft(null);
    setReview({ phase: "idle" });
  }
  return {
    values: draft ? { ...draft.snapshot, ...draft.changes } : config ? policy(config) : null,
    begin,
    edit,
    candidate,
    save,
    review,
    reviewLatest,
    adopt,
    isPending: command.isPending,
    blocked: review.phase !== "idle",
  };
}
