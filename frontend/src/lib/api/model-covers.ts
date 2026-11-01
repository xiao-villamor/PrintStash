import { authHeaders, handleResponse, jsonHeaders, requestApi } from "@/lib/api/request";
import { editHeaders, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import type { ModelRead } from "@/types";

export type ModelCoverCommand =
  | { kind: "upload"; file: File }
  | { kind: "source"; sourceId: number }
  | { kind: "reset" };

export function saveModelCover(
  id: number,
  command: ModelCoverCommand,
  base: EditingBase,
): Promise<ModelRead> {
  const path = `/api/v1/models/${id}/cover`;
  const headers = editHeaders("model", id, base);
  const form = new FormData();
  if (command.kind === "upload") form.append("file", command.file);
  const request: RequestInit =
    command.kind === "upload"
      ? { method: "PUT", headers: { ...authHeaders(), ...headers }, body: form }
      : command.kind === "source"
        ? {
            method: "POST",
            headers: { ...jsonHeaders(), ...headers },
            body: JSON.stringify({ source_id: command.sourceId }),
          }
        : { method: "DELETE", headers: { ...authHeaders(), ...headers } };
  return requestApi(
    command.kind === "source" ? path + "/source" : path,
    request,
    async (response, session) => {
      const saved = await handleResponse<ModelRead>(response, session);
      requireEditingReceipt(saved, base);
      if (saved.id !== id) throw new Error("Invalid Model cover acknowledgement");
      return saved;
    },
  );
}
