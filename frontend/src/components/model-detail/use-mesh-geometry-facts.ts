import { skipToken, useQuery } from "@tanstack/react-query";
import { listDerivatives } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import type { FileRead } from "@/types";

type MeasurementStatus = "pending" | "unavailable";

function meshDimensions(file: FileRead | null) {
  const metadata = file?.metadata;
  return metadata?.bbox_x_mm != null && metadata.bbox_y_mm != null && metadata.bbox_z_mm != null
    ? [metadata.bbox_x_mm, metadata.bbox_y_mm, metadata.bbox_z_mm]
    : null;
}

export function useMeshGeometryMeasurement(file: FileRead | null): MeasurementStatus {
  const needsMeasurement = meshDimensions(file) === null || file?.metadata?.triangle_count == null;
  const status = useQuery({
    queryKey: ["mesh-measurement", file?.id, file?.sha256],
    queryFn: file === null ? skipToken : () => listDerivatives(file.id),
    enabled: file !== null && needsMeasurement,
    select: (rows) => rows.find((row) => row.kind === "metadata")?.state ?? null,
    refetchInterval: (query) => {
      const state = query.state.data?.find((row) => row.kind === "metadata")?.state;
      return state === "pending" || state === "queued" || state === "running" ? 3000 : false;
    },
  });
  return !needsMeasurement ||
    status.isError ||
    status.data === "failed" ||
    status.data === "cancelled" ||
    status.data === "skipped" ||
    status.data === "ready" ||
    status.data === null
    ? "unavailable"
    : "pending";
}

export function meshGeometryFacts(file: FileRead | null, status: MeasurementStatus) {
  const metadata = file?.metadata;
  const dimensions = meshDimensions(file);
  const missing = uiText(status === "pending" ? "Measuring geometry…" : "Measurement unavailable");
  const facts: [string, string][] = [
    [
      uiText("Dimensions"),
      dimensions === null
        ? missing
        : uiText("{value1} mm", {
            value1: dimensions.map((value) => formatNumber(value)).join(" × "),
          }),
    ],
    [
      uiText("Volume"),
      metadata?.volume_mm3 != null
        ? uiText("{value} cm³", {
            value: formatNumber(metadata.volume_mm3 / 1000, { maximumFractionDigits: 2 }),
          })
        : missing,
    ],
    [
      uiText("Triangles"),
      metadata?.triangle_count != null ? formatNumber(metadata.triangle_count) : missing,
    ],
  ];
  const empty =
    dimensions === null && metadata?.volume_mm3 == null && metadata?.triangle_count == null;
  return { facts, emptyMessage: empty ? missing : null };
}
