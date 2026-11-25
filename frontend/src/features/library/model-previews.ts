import { useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/models";
import { queryKeys } from "@/lib/query-client";
import { withSessionRequest } from "@/lib/session-transport";

/** Recreating views queues producer work; the library and the activity count follow. */
export function useRegenerateModelPreviews() {
  const client = useQueryClient();
  return (id: number) =>
    withSessionRequest(async (request) => {
      const receipt = await api.regenerateModelPreviews(id);
      request.assertCurrent();
      void client.invalidateQueries({ queryKey: queryKeys.previewActivity });
      void client.invalidateQueries({ queryKey: queryKeys.models });
      return receipt;
    });
}
