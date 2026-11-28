import { useMemo, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getJson, sendJson } from "@/lib/api/request";
import { isLoggedIn, onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { PrinterRead } from "@/types";

import {
  HEATER_SAMPLE_LIMIT,
  HEATER_WINDOW_MS,
  type HeaterHistory,
  type HeaterSample,
} from "./telemetry";

interface RecordedHeaterSample {
  age_ms: number;
  temperature: number | null;
  target: number | null;
}

export interface RecordedHeaterHistory {
  sample_interval_ms: number;
  extruder: readonly RecordedHeaterSample[];
  heater_bed: readonly RecordedHeaterSample[];
}

export interface FetchedHeaterHistory {
  samples: RecordedHeaterHistory;
  startedAt: number;
  receivedAt: number;
}

interface ActiveBedMesh {
  state: "active";
  profile_name: string | null;
  mesh_min: readonly [number, number];
  mesh_max: readonly [number, number];
  probed_matrix: readonly (readonly number[])[];
  variation_mm: number;
}

export type BedMesh = { state: "unconfigured" } | { state: "inactive" } | ActiveBedMesh;

export interface DisplayHeaterHistory {
  history: HeaterHistory;
  recorded: boolean;
}

export function prependRecordedHeaters(
  live: HeaterHistory,
  fetched: FetchedHeaterHistory | undefined,
): DisplayHeaterHistory {
  if (!fetched) return { history: live, recorded: false };
  const { receivedAt, startedAt, samples } = fetched;
  const interval = samples.sample_interval_ms;

  function prepend(
    observed: HeaterHistory[keyof HeaterHistory],
    stored: readonly RecordedHeaterSample[],
  ): HeaterHistory[keyof HeaterHistory] {
    const end = Math.max(receivedAt, observed.at(-1)?.at ?? receivedAt);
    // Prefer the live series throughout any overlap, including uncertainty from
    // the request duration and Moonraker's untimestamped sampling phase.
    const cutoff = observed.length
      ? observed[0].at - interval - Math.max(0, receivedAt - startedAt)
      : Infinity;
    const historical: HeaterSample[] = [];
    let previousAt: number | undefined;
    // Distinct from the nonnegative live connection IDs. Missing measurements
    // break the historical segment even if that slot reported a target.
    let connection = -1;
    for (const sample of stored) {
      const at = receivedAt - sample.age_ms;
      if (at < end - HEATER_WINDOW_MS || at >= cutoff) continue;
      if (sample.temperature === null) {
        connection -= 1;
        previousAt = undefined;
        continue;
      }
      if (previousAt !== undefined && at - previousAt > interval) connection -= 1;
      historical.push({ at, degrees: sample.temperature, connection });
      previousAt = at;
    }
    const space = HEATER_SAMPLE_LIMIT - observed.length;
    if (historical.length === 0 || space <= 0) return observed;
    return [...historical.slice(-space), ...observed];
  }

  const extruder = prepend(live.extruder, samples.extruder);
  const heater_bed = prepend(live.heater_bed, samples.heater_bed);
  if (extruder === live.extruder && heater_bed === live.heater_bed) {
    return { history: live, recorded: false };
  }
  return { history: { extruder, heater_bed }, recorded: true };
}

const onDemand = {
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
} as const;

export function usePrinterHeaterHistory(
  printer: Pick<PrinterRead, "id" | "provider" | "access" | "status"> | null,
  live: HeaterHistory,
): DisplayHeaterHistory {
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const id = printer?.id;
  const enabled =
    isLoggedIn() &&
    printer?.provider === "moonraker" &&
    printer.access.can_view &&
    printer.status !== "offline";
  const query = useQuery({
    ...onDemand,
    queryKey: ["printer-inspection", id, session, "heater-history"],
    enabled,
    staleTime: 0,
    queryFn: async ({ signal }): Promise<FetchedHeaterHistory> => {
      const startedAt = Date.now();
      const samples = await getJson<RecordedHeaterHistory>(
        `/api/v1/printers/${id}/heater-history`,
        { signal },
      );
      return { samples, startedAt, receivedAt: Date.now() };
    },
  });
  const fetched = enabled ? query.data : undefined;
  return useMemo(() => prependRecordedHeaters(live, fetched), [live, fetched]);
}

export function usePrinterBedMesh(printerId: number, canCheckMeasurement: boolean) {
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const queryClient = useQueryClient();
  const meshKey = ["printer-inspection", printerId, session, "bed-mesh"] as const;
  const path = `/api/v1/printers/${printerId}/bed-mesh`;
  const mesh = useQuery({
    ...onDemand,
    queryKey: meshKey,
    enabled: isLoggedIn(),
    staleTime: 0,
    queryFn: ({ signal }) => getJson<BedMesh>(path, { signal }),
  });
  const availability = useQuery({
    ...onDemand,
    queryKey: [...meshKey, "measurement"],
    enabled: isLoggedIn() && canCheckMeasurement,
    staleTime: 0,
    queryFn: ({ signal }) => getJson<{ supported: boolean }>(`${path}/measurement`, { signal }),
  });

  async function measure() {
    requireSessionVersion(session);
    const measured = await sendJson<ActiveBedMesh>(`${path}/measurement`, "POST", {
      confirmed: true,
    });
    await queryClient.cancelQueries({ queryKey: meshKey, exact: true });
    requireSessionVersion(session);
    queryClient.setQueryData<BedMesh>(meshKey, measured);
  }

  return { mesh, availability, measure, session };
}
