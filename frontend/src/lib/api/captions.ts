import { getJson, sendJson } from "@/lib/api/request";
import type { CaptionPatch, SubjectCaption } from "@/types/captions";
import type { SearchSubjectType } from "@/types/search";

export function getCaption(type: SearchSubjectType, id: number) {
  return getJson<SubjectCaption>(`/api/v1/subjects/${type}/${id}/caption`, { fresh: true });
}
export function patchCaption(type: SearchSubjectType, id: number, body: CaptionPatch) {
  return sendJson<SubjectCaption>(`/api/v1/subjects/${type}/${id}/caption`, "PATCH", body);
}
