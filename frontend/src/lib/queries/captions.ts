/** Caption snapshots and conditional acknowledgements have one private feature owner. */
import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { getCaption, patchCaption } from "@/lib/api/captions";
import { requireSessionVersion } from "@/lib/session-transport";
import type { CaptionPatch } from "@/types/captions";
import type { SearchSubjectType } from "@/types/search";

export const captionKeys = {
  detail: (userId: number, type: SearchSubjectType, id: number) =>
    ["subject-caption", userId, type, id] as const,
};
export function subjectCaptionOptions(userId: number, type: SearchSubjectType, id: number) {
  return queryOptions({
    queryKey: captionKeys.detail(userId, type, id),
    queryFn: ({ signal }) => getCaption(type, id, { signal }),
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      (query.state.data?.phase === "pending" || query.state.data?.phase === "running")
        ? 5000
        : false,
  });
}
export interface CaptionCommand {
  userId: number;
  type: SearchSubjectType;
  id: number;
  session: number;
  body: CaptionPatch;
}
export function useCaptionCommand() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: ({ type, id, session, body }: CaptionCommand) => {
      requireSessionVersion(session);
      return patchCaption(type, id, body);
    },
    onMutate: async ({ userId, type, id, session }) => {
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: captionKeys.detail(userId, type, id) });
      requireSessionVersion(session);
      return session;
    },
    onSuccess: async (caption, { userId, type, id }, session) => {
      if (session === undefined) throw new Error("A caption command requires its session");
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: captionKeys.detail(userId, type, id) });
      requireSessionVersion(session);
      client.setQueryData(captionKeys.detail(userId, type, id), caption);
      for (const queryKey of [
        ["search-results", userId],
        ["search-suggestions", userId],
        ["ai-search", "status", userId],
      ]) {
        requireSessionVersion(session);
        void client.invalidateQueries({ queryKey });
      }
    },
  });
}
