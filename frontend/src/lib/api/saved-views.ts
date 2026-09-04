import { getJson, sendAction, sendJson } from "@/lib/api/request";
import type { SavedViewFilters, SavedViewRead } from "@/types";

export const listSavedViews = () =>
  getJson<SavedViewRead[]>("/api/v1/saved-views", { fresh: true });
export const createSavedView = (name: string, filters: SavedViewFilters) =>
  sendJson<SavedViewRead>("/api/v1/saved-views", "POST", { name, filters });
export const updateSavedView = (
  id: number,
  payload: { name?: string; filters?: SavedViewFilters },
) => sendJson<SavedViewRead>(`/api/v1/saved-views/${id}`, "PATCH", payload);
export const deleteSavedView = (id: number) => sendAction(`/api/v1/saved-views/${id}`, "DELETE");
