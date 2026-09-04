import { getJson, GetJsonOptions } from "@/lib/api/request";
import { PrintStatisticsRead } from "@/types";

export type StatsPeriod = "7d" | "30d" | "90d" | "1y" | "all";

export function getPrintStatistics(
  period: StatsPeriod,
  options?: GetJsonOptions,
): Promise<PrintStatisticsRead> {
  return getJson<PrintStatisticsRead>(`/api/v1/models/stats/prints?period=${period}`, options);
}
