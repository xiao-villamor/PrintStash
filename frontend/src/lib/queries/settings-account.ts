/** Account snapshots are authoritative; an issued secret is a local receipt only. */
import { useEffect, useRef, useState } from "react";
import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listApiKeys,
  listAdminUsers,
  createApiKey,
  revokeApiKey,
  createAdminUser,
  updateAdminUser,
  resetAdminUserPassword,
  deactivateAdminUser,
} from "@/lib/api/auth";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { onAuthChange } from "@/lib/auth-store";
import type { ApiKeyRead, UserRead, UserCreate, UserUpdate } from "@/types";

export const accountKeys = {
  apiKeys: (userId: number | null) => ["api-keys", userId] as const,
  users: (userId: number | null) => ["admin", "users", userId] as const,
};
export function apiKeysOptions(userId: number | null) {
  return queryOptions({
    queryKey: accountKeys.apiKeys(userId),
    queryFn: ({ signal }) => {
      if (userId === null) throw new Error("API keys require an authenticated reader");
      return listApiKeys({ signal });
    },
    enabled: userId !== null,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
}
export function adminUsersOptions(userId: number | null) {
  return queryOptions({
    queryKey: accountKeys.users(userId),
    queryFn: ({ signal }) => {
      if (userId === null) throw new Error("Admin Users require an authorized reader");
      return listAdminUsers({ signal });
    },
    enabled: userId !== null,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
}
interface AccountGesture {
  userId: number;
  session: number;
}
export interface ApiKeyReceipt {
  keyId: number;
  secret: string;
  session: number;
  userId: number;
}
export type ApiKeyCommand = AccountGesture &
  (
    | { kind: "create"; name: string; receiveReceipt: (receipt: ApiKeyReceipt) => void }
    | { kind: "revoke"; id: number }
  );
export function useApiKeyCommand() {
  const client = useQueryClient();
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  return useMutation({
    retry: false,
    onMutate: async ({ userId, session }: ApiKeyCommand) => {
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: accountKeys.apiKeys(userId) });
      requireSessionVersion(session);
    },
    mutationFn: async (command: ApiKeyCommand): Promise<ApiKeyRead | null> => {
      requireSessionVersion(command.session);
      const key = accountKeys.apiKeys(command.userId);
      if (command.kind === "create") {
        const result = await createApiKey(command.name);
        requireSessionVersion(command.session);
        await client.cancelQueries({ queryKey: key });
        requireSessionVersion(command.session);
        const { api_key: secret, ...row } = result;
        client.setQueryData<ApiKeyRead[]>(key, (rows) => {
          requireSessionVersion(command.session);
          return rows ? [row, ...rows.filter((item) => item.id !== row.id)] : rows;
        });
        requireSessionVersion(command.session);
        if (live.current)
          command.receiveReceipt({
            keyId: row.id,
            secret,
            session: command.session,
            userId: command.userId,
          });
        // A shared MutationCache retains only the nonsecret server read projection.
        return row;
      }
      await revokeApiKey(command.id);
      requireSessionVersion(command.session);
      await client.cancelQueries({ queryKey: key });
      requireSessionVersion(command.session);
      client.setQueryData<ApiKeyRead[]>(key, (rows) => {
        requireSessionVersion(command.session);
        return rows?.filter((row) => row.id !== command.id);
      });
      return null;
    },
  });
}
export type AdminUserCommand = AccountGesture &
  (
    | { kind: "create"; payload: UserCreate }
    | { kind: "update"; id: number; payload: UserUpdate }
    | { kind: "password"; id: number; password: string }
    | { kind: "deactivate"; id: number }
  );
type AdminPendingCommand =
  | { kind: "create" }
  | { kind: "update" | "password" | "deactivate"; id: number };
type AdminCommandState =
  | { status: "idle" | "success" }
  | { status: "pending"; command: AdminPendingCommand }
  | { status: "error"; error: unknown };
type AdminCommandOwner = {
  status: AdminCommandState["status"];
  isError: boolean;
  error: unknown;
  mutate: (command: AdminUserCommand) => void;
  mutateAsync: (command: AdminUserCommand) => Promise<UserRead | null>;
} & (
  | { isPending: true; variables: AdminPendingCommand }
  | { isPending: false; variables: undefined }
);

/** Password-bearing commands exist only for their active call, never in MutationCache. */
export function useAdminUserCommand(): AdminCommandOwner {
  const client = useQueryClient();
  const [state, setState] = useState<AdminCommandState>({ status: "idle" });
  const live = useRef(true);
  const active = useRef<object | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current = null;
      setState({ status: "idle" });
    });
    return () => {
      live.current = false;
      active.current = null;
      release();
    };
  }, []);

  async function mutateAsync(command: AdminUserCommand): Promise<UserRead | null> {
    if (!live.current) throw new DOMException("Account view was disposed", "AbortError");
    if (active.current !== null) throw new Error("An account command is already pending");
    const token = {};
    active.current = token;
    setState({
      status: "pending",
      command:
        command.kind === "create" ? { kind: "create" } : { kind: command.kind, id: command.id },
    });
    const key = accountKeys.users(command.userId);
    try {
      requireSessionVersion(command.session);
      await client.cancelQueries({ queryKey: key });
      requireSessionVersion(command.session);
      let row: UserRead | null;
      if (command.kind === "deactivate") {
        await deactivateAdminUser(command.id);
        requireSessionVersion(command.session);
        await client.cancelQueries({ queryKey: key });
        requireSessionVersion(command.session);
        // DELETE deactivates and returns204, so its updated DTO needs one authoritative read.
        void client.invalidateQueries({ queryKey: key });
        row = null;
      } else {
        row =
          command.kind === "create"
            ? await createAdminUser(command.payload)
            : command.kind === "update"
              ? await updateAdminUser(command.id, command.payload)
              : await resetAdminUserPassword(command.id, { password: command.password });
        requireSessionVersion(command.session);
        await client.cancelQueries({ queryKey: key });
        requireSessionVersion(command.session);
        const acknowledged = row;
        client.setQueryData<UserRead[]>(key, (rows) => {
          requireSessionVersion(command.session);
          if (!rows) return rows;
          const next = rows.some((item) => item.id === acknowledged.id)
            ? rows.map((item) => (item.id === acknowledged.id ? acknowledged : item))
            : command.kind === "create"
              ? [...rows, acknowledged]
              : rows;
          return [...next].sort((a, b) =>
            a.username < b.username ? -1 : a.username > b.username ? 1 : 0,
          );
        });
      }
      if (live.current && active.current === token && command.session === getSessionVersion())
        setState({ status: "success" });
      return row;
    } catch (error) {
      if (live.current && active.current === token && command.session === getSessionVersion())
        setState({ status: "error", error });
      throw error;
    } finally {
      if (active.current === token) active.current = null;
    }
  }
  function mutate(command: AdminUserCommand) {
    // The local error state is the observable result for fire-and-observe callers.
    void mutateAsync(command).catch(() => {});
  }
  const result = {
    status: state.status,
    isError: state.status === "error",
    error: state.status === "error" ? state.error : null,
    mutate,
    mutateAsync,
  };
  return state.status === "pending"
    ? { ...result, isPending: true, variables: state.command }
    : { ...result, isPending: false, variables: undefined };
}
