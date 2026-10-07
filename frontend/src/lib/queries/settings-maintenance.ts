/** Shared audit observations and entry-scoped operational commands. */
import { useEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/maintenance";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError } from "@/lib/errors";
import {
  getSessionVersion,
  withSessionRequest,
  type SessionRequest,
} from "@/lib/session-transport";
import type {
  AuditPolicy,
  VaultAuditRun,
  VaultAuditFinding,
  VaultAuditMode,
} from "@/types/maintenance";

export const maintenanceKeys = {
  audits: ["maintenance", "audits"] as const,
  policies: ["maintenance", "policies"] as const,
};
export function isActiveAudit(run: VaultAuditRun | null | undefined) {
  return (
    run?.state === "pending" ||
    run?.state === "running" ||
    (run?.state === "completed" && run.current_phase === "auto_repair")
  );
}
export function auditHistoryOptions() {
  return queryOptions({
    queryKey: maintenanceKeys.audits,
    queryFn: ({ signal }) => api.listVaultAudits({ signal }),
    retry: false,
    refetchInterval: (query) =>
      [401, 403, 404].includes(parseApiError(query.state.error).status)
        ? false
        : query.state.data?.some(isActiveAudit)
          ? 1500
          : false,
  });
}
export function auditPoliciesOptions() {
  return queryOptions({
    queryKey: maintenanceKeys.policies,
    queryFn: ({ signal }) => api.listAuditPolicies({ signal }),
    retry: false,
  });
}
export function useMaintenanceCommands() {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const [retired, setRetired] = useState(false);
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const dispose = onAuthChange(() => {
      active.current?.abort();
      setRetired(true);
    });
    return () => {
      live.current = false;
      active.current?.abort();
      dispose();
    };
  }, []);
  const current = () => live.current && session === getSessionVersion();
  async function execute<T>(
    key: readonly string[],
    operation: (request: SessionRequest) => Promise<T>,
    publish: (receipt: T) => void,
  ) {
    if (!current()) throw new DOMException("Maintenance entry retired", "AbortError");
    if (active.current) throw new Error("A maintenance command is pending");
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    try {
      return await withSessionRequest(async (request) => {
        await client.cancelQueries({ queryKey: key });
        request.assertCurrent();
        const receipt = await operation(request);
        request.assertCurrent();
        await client.cancelQueries({ queryKey: key });
        request.assertCurrent();
        publish(receipt);
        return receipt;
      }, controller.signal);
    } finally {
      if (active.current === controller) active.current = null;
      if (current()) setBusy(false);
    }
  }
  function publishRun(receipt: VaultAuditRun) {
    client.setQueryData<VaultAuditRun[]>(maintenanceKeys.audits, (rows = []) =>
      [receipt, ...rows.filter((row) => row.id !== receipt.id)].sort((a, b) => b.id - a.id),
    );
  }
  function publishFinding(receipt: VaultAuditFinding) {
    client.setQueryData<VaultAuditRun[]>(maintenanceKeys.audits, (rows) =>
      rows?.map((run) =>
        run.id === receipt.run_id
          ? {
              ...run,
              findings: run.findings.map((finding) =>
                finding.id === receipt.id ? receipt : finding,
              ),
            }
          : run,
      ),
    );
    void client.invalidateQueries({ queryKey: maintenanceKeys.audits });
  }
  function publishPolicy(receipt: AuditPolicy) {
    client.setQueryData<AuditPolicy[]>(maintenanceKeys.policies, (rows) =>
      rows?.map((row) =>
        row.mode === receipt.mode && row.revision <= receipt.revision ? receipt : row,
      ),
    );
  }
  return {
    busy,
    current,
    retired,
    start: (mode: VaultAuditMode) =>
      execute(
        maintenanceKeys.audits,
        (request) => api.startVaultAudit(mode, { signal: request.signal }),
        publishRun,
      ),
    cancel: (id: number) =>
      execute(
        maintenanceKeys.audits,
        (request) => api.cancelVaultAudit(id, { signal: request.signal }),
        publishRun,
      ),
    repair: (id: number) =>
      execute(
        maintenanceKeys.audits,
        (request) => api.repairAuditFinding(id, { signal: request.signal }),
        publishFinding,
      ),
    ignore: (id: number) =>
      execute(
        maintenanceKeys.audits,
        (request) => api.ignoreAuditFinding(id, { signal: request.signal }),
        publishFinding,
      ),
    savePolicy: (policy: AuditPolicy) =>
      execute(
        maintenanceKeys.policies,
        (request) => api.saveAuditPolicy(policy, { signal: request.signal }),
        publishPolicy,
      ),
    skip: (mode: VaultAuditMode) =>
      execute(
        maintenanceKeys.policies,
        (request) => api.skipAuditSlot(mode, { signal: request.signal }),
        publishPolicy,
      ),
    verify: (id: string, source: string) =>
      execute(
        ["maintenance", "verification", source],
        (request) => api.verifyBackup(id, source, { signal: request.signal }),
        () => {},
      ),
  };
}
