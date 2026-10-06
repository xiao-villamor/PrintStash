import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import type { CaptionPatch, SubjectCaption } from "@/types/captions";
import type { SearchSubjectType } from "@/types/search";

export function getCaption(type: SearchSubjectType, id: number, options: GetJsonOptions = {}) {
  return getJson<SubjectCaption>(`/api/v1/subjects/${type}/${id}/caption`, {
    ...options,
    fresh: true,
  });
}
export function patchCaption(type: SearchSubjectType, id: number, body: CaptionPatch) {
  return requestApi<SubjectCaption>(`/api/v1/subjects/${type}/${id}/caption`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
}
