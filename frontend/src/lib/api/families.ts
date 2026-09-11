import {
  authHeaders,
  getJson,
  getUrl,
  handleResponse,
  invalidateApiCache,
  sendAction,
  sendJson,
} from "./request";
import type { ModelBatchResult } from "@/types/models";
import type {
  FamilyBrowseCard,
  FamilyBrowseParams,
  FamilyCreate,
  FamilyListParams,
  FamilyMemberInput,
  FamilyMemberItem,
  FamilyMemberParams,
  FamilyMemberRead,
  FamilyPage,
  FamilyRead,
  FamilyUpdate,
  MemberRole,
} from "@/types/families";

function query(values: FamilyListParams | FamilyMemberParams | FamilyBrowseParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null) continue;
    for (const item of Array.isArray(value) ? value : [value]) search.append(key, String(item));
  }
  return search.size ? `?${search}` : "";
}

export function getFamily(id: number) {
  return getJson<FamilyRead>(`/api/v1/families/${id}`, { fresh: true });
}
export function getFamilyBySlug(slug: string) {
  return getJson<FamilyRead>(`/api/v1/families/by-slug/${encodeURIComponent(slug)}`, {
    fresh: true,
  });
}
export function listFamilies(params: FamilyListParams = {}) {
  return getJson<FamilyPage<FamilyRead>>(`/api/v1/families${query(params)}`, { fresh: true });
}
export function browseFamilies(params: FamilyBrowseParams = {}) {
  return getJson<FamilyPage<FamilyBrowseCard>>(`/api/v1/families/browse${query(params)}`, {
    fresh: true,
  });
}
export function listFamilyMembers(id: number, params: FamilyMemberParams = {}) {
  return getJson<FamilyPage<FamilyMemberItem>>(`/api/v1/families/${id}/members${query(params)}`, {
    fresh: true,
  });
}
export function createFamily(data: FamilyCreate) {
  return sendJson<FamilyRead>("/api/v1/families", "POST", data);
}
export function updateFamily(id: number, data: FamilyUpdate) {
  return sendJson<FamilyRead>(`/api/v1/families/${id}`, "PATCH", data);
}
export function addFamilyMember(id: number, version: number, data: FamilyMemberInput) {
  return sendJson<FamilyMemberRead>(`/api/v1/families/${id}/members`, "POST", { ...data, version });
}
export function updateFamilyMember(
  id: number,
  memberId: number,
  version: number,
  data: Omit<FamilyMemberInput, "model_id">,
) {
  return sendJson<FamilyMemberRead>(`/api/v1/families/${id}/members/${memberId}`, "PATCH", {
    ...data,
    version,
  });
}
export function detachFamilyMember(id: number, memberId: number, version: number) {
  return sendAction(`/api/v1/families/${id}/members/${memberId}?version=${version}`, "DELETE");
}
export function setFamilyCanonical(
  id: number,
  memberId: number,
  previousRole: MemberRole,
  version: number,
) {
  return sendJson<FamilyRead>(`/api/v1/families/${id}/canonical`, "POST", {
    member_id: memberId,
    previous_role: previousRole,
    version,
  });
}
export function moveFamilyMember(
  id: number,
  data: FamilyMemberInput & {
    source_family_id: number;
    source_version: number;
    destination_version: number;
  },
) {
  return sendJson<FamilyMemberRead>(`/api/v1/families/${id}/move-member`, "POST", data);
}
export function trashFamily(id: number, version: number) {
  return sendAction(`/api/v1/families/${id}?version=${version}`, "DELETE");
}
export function purgeFamily(id: number, version: number) {
  return sendAction(`/api/v1/families/${id}/purge?version=${version}`, "DELETE");
}
export function restoreFamily(id: number, version: number) {
  return sendJson<{ family: FamilyRead; omitted_member_ids: number[] }>(
    `/api/v1/families/${id}/restore`,
    "POST",
    { version },
  );
}
export function starFamily(id: number, starred: boolean) {
  return starred
    ? sendJson<void>(`/api/v1/families/${id}/star`, "PUT", {})
    : sendAction(`/api/v1/families/${id}/star`, "DELETE");
}
export function tagFamilyModels(id: number, version: number, add: string[], remove: string[] = []) {
  return sendJson<ModelBatchResult>(`/api/v1/families/${id}/members/tags`, "POST", {
    version,
    add,
    remove,
  });
}
export function moveFamilyModels(id: number, version: number, collection: string) {
  return sendJson<ModelBatchResult>(`/api/v1/families/${id}/members/collection`, "POST", {
    version,
    collection,
  });
}
export function starVisibleFamilyModels(id: number, version: number) {
  return sendJson<ModelBatchResult>(`/api/v1/families/${id}/members/star`, "POST", { version });
}
export async function uploadFamilyCover(id: number, version: number, file: File) {
  const body = new FormData();
  body.append("file", file);
  const result = await handleResponse<FamilyRead>(
    await fetch(getUrl(`/api/v1/families/${id}/cover?version=${version}`), {
      method: "PUT",
      headers: authHeaders(),
      body,
    }),
  );
  invalidateApiCache(`/api/v1/families/${id}/cover`);
  return result;
}
export async function removeFamilyCover(id: number, version: number) {
  const result = await handleResponse<FamilyRead>(
    await fetch(getUrl(`/api/v1/families/${id}/cover?version=${version}`), {
      method: "DELETE",
      headers: authHeaders(),
    }),
  );
  invalidateApiCache(`/api/v1/families/${id}/cover`);
  return result;
}
