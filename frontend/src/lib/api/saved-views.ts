import { getJson, sendAction, sendJson, type GetJsonOptions } from "@/lib/api/request";
import type { SavedViewFilters, SavedViewRead } from "@/types";

export const listSavedViews = (options?: GetJsonOptions) =>
  getJson<SavedViewRead[]>("/api/v1/saved-views", options);
export const createSavedView = (name: string, filters: SavedViewFilters) =>
  sendJson<SavedViewRead>("/api/v1/saved-views", "POST", { name, filters });
export const updateSavedView = (
  id: number,
  payload: { name?: string; filters?: SavedViewFilters },
) => sendJson<SavedViewRead>(`/api/v1/saved-views/${id}`, "PATCH", payload);
export const deleteSavedView = (id: number) => sendAction(`/api/v1/saved-views/${id}`, "DELETE");
