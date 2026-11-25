import { useQuery } from "@tanstack/react-query";
import { getJson } from "@/lib/api/request";
import { queryKeys } from "@/lib/query-client";
import type { CaptureProfile } from "@/types/inbox";

export interface RelatedProfile {
  model_id: number;
  model_name: string;
  file_id: number;
  filename: string;
  provider: string;
  captured_at: string;
  profile: CaptureProfile;
}
function relatedProfileOptions(modelId: number) {
  return {
    queryKey: [...queryKeys.model(modelId), "related-profiles"],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      getJson<{ items: RelatedProfile[]; has_more: boolean }>(
        `/api/v1/models/${modelId}/related-profiles`,
        { signal },
      ),
    retry: false as const,
  };
}

export function useRelatedProfiles(modelId: number, enabled = true) {
  return useQuery({ ...relatedProfileOptions(modelId), enabled });
}
