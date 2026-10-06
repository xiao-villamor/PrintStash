/** Account snapshots are authoritative; an issued secret is a local receipt only. */
import { useEffect, useRef } from "react";
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
import { requireSessionVersion } from "@/lib/session-transport";
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
export function useAdminUserCommand() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    onMutate: async ({ userId, session }: AdminUserCommand) => {
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: accountKeys.users(userId) });
      requireSessionVersion(session);
    },
    mutationFn: async (command: AdminUserCommand): Promise<UserRead | null> => {
      requireSessionVersion(command.session);
      const key = accountKeys.users(command.userId);
      if (command.kind === "deactivate") {
        await deactivateAdminUser(command.id);
        requireSessionVersion(command.session);
        await client.cancelQueries({ queryKey: key });
        requireSessionVersion(command.session);
        // DELETE deactivates and returns204, so the actual updated DTO needs one read.
        void client.invalidateQueries({ queryKey: key });
        return null;
      }
      const row =
        command.kind === "create"
          ? await createAdminUser(command.payload)
          : command.kind === "update"
            ? await updateAdminUser(command.id, command.payload)
            : await resetAdminUserPassword(command.id, { password: command.password });
      requireSessionVersion(command.session);
      await client.cancelQueries({ queryKey: key });
      requireSessionVersion(command.session);
      client.setQueryData<UserRead[]>(key, (rows) => {
        requireSessionVersion(command.session);
        if (!rows) return rows;
        const next = rows.some((item) => item.id === row.id)
          ? rows.map((item) => (item.id === row.id ? row : item))
          : command.kind === "create"
            ? [...rows, row]
            : rows;
        return [...next].sort((a, b) =>
          a.username < b.username ? -1 : a.username > b.username ? 1 : 0,
        );
      });
      return row;
    },
  });
}
