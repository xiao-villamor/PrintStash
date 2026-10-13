import { queryOptions } from "@tanstack/react-query";
import { getPrintStatistics, type StatsPeriod } from "@/lib/api/statistics";
import { queryKeys } from "@/lib/query-client";

export const statisticsKeys = {
  all: ["print-stats"] as const,
  period: queryKeys.printStats,
};

export function printStatisticsOptions(
  period: StatsPeriod,
  read: typeof getPrintStatistics = getPrintStatistics,
) {
  return queryOptions({
    queryKey: statisticsKeys.period(period),
    queryFn: ({ signal }) => read(period, { signal }),
  });
}
