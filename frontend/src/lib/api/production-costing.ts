import { editHeaders, requireEditingReceipt } from "./editing";
import { getJson, sendAction, sendJson } from "./request";
import type { EditingBase } from "@/types/editing";
import type {
  ArtifactCostingRead,
  PricingTemplate,
  ProductionInputs,
} from "@/types/production-costing";

const costPath = (fileId: number) => `/api/v1/files/${fileId}/production-cost`;
const templatesPath = "/api/v1/pricing-templates";
export const pricingPageSize = 50;

export function readProductionCost(fileId: number, signal?: AbortSignal) {
  return getJson<ArtifactCostingRead>(costPath(fileId), { signal });
}

export async function saveProductionCost(
  modelId: number,
  fileId: number,
  estimate: ProductionInputs | null,
  base: EditingBase,
) {
  const receipt = await sendJson<ArtifactCostingRead>(
    costPath(fileId),
    "PUT",
    { estimate },
    editHeaders("model", modelId, base),
  );
  requireEditingReceipt(receipt, base);
  return receipt;
}

export function listPricingTemplates(offset: number, signal: AbortSignal) {
  return getJson<PricingTemplate[]>(
    `${templatesPath}?offset=${offset}&limit=${pricingPageSize + 1}`,
    { signal },
  );
}

export function createPricingTemplate(template: Omit<PricingTemplate, "id">) {
  return sendJson<PricingTemplate>(templatesPath, "POST", template);
}

export function deletePricingTemplate(id: string) {
  return sendAction(`${templatesPath}/${encodeURIComponent(id)}`, "DELETE");
}
