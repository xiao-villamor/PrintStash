import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createPricingTemplate,
  deletePricingTemplate,
  listPricingTemplates,
  readProductionCost,
  saveProductionCost,
} from "@/lib/api/production-costing";
import { queryKeys } from "@/lib/query-client";
import { requireSessionVersion } from "@/lib/session-transport";
import type { EditingBase } from "@/types/editing";
import type { PricingTemplate, ProductionInputs } from "@/types/production-costing";

const templatesKey = ["pricing-templates"] as const;

export function productionCostOptions(modelId: number, fileId: number) {
  return queryOptions({
    queryKey: [...queryKeys.model(modelId), "production-cost", fileId],
    queryFn: ({ signal }) => readProductionCost(fileId, signal),
  });
}

export function pricingTemplatesOptions(offset: number) {
  return queryOptions({
    queryKey: [...templatesKey, offset],
    queryFn: ({ signal }) => listPricingTemplates(offset, signal),
  });
}

export function useProductionCostCommands(modelId: number, fileId: number) {
  const client = useQueryClient();
  const { queryKey } = productionCostOptions(modelId, fileId);
  const refreshTemplates = async (session: number) => {
    requireSessionVersion(session);
    await client.invalidateQueries({ queryKey: templatesKey });
    requireSessionVersion(session);
  };
  return {
    save: useMutation({
      mutationFn: async ({
        estimate,
        base,
        session,
      }: {
        estimate: ProductionInputs | null;
        base: EditingBase;
        session: number;
      }) => {
        await client.cancelQueries({ queryKey, exact: true });
        requireSessionVersion(session);
        return saveProductionCost(modelId, fileId, estimate, base);
      },
      onSuccess: async (receipt, { session }) => {
        await client.cancelQueries({ queryKey, exact: true });
        requireSessionVersion(session);
        client.setQueryData(queryKey, receipt);
        void client.invalidateQueries({ queryKey });
        void client.invalidateQueries({ queryKey: queryKeys.model(modelId), exact: true });
      },
    }),
    createTemplate: useMutation({
      mutationFn: ({
        template,
        session,
      }: {
        template: Omit<PricingTemplate, "id">;
        session: number;
      }) => {
        requireSessionVersion(session);
        return createPricingTemplate(template);
      },
      onSuccess: (_, { session }) => refreshTemplates(session),
    }),
    deleteTemplate: useMutation({
      mutationFn: ({ id, session }: { id: string; session: number }) => {
        requireSessionVersion(session);
        return deletePricingTemplate(id);
      },
      onSuccess: (_, { session }) => refreshTemplates(session),
    }),
  };
}
