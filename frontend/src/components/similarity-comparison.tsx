import { lazy, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Checkbox } from "@/components/ui/checkbox";
import { getModelPrintJobs } from "@/lib/api/models";
import { getAssetUrl } from "@/lib/api/request";
import { createComparisonCamera } from "@/lib/comparison-camera";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import type { FileRead, ModelRead } from "@/types/models";
import type { SimilarityCandidate, SimilaritySource } from "@/types/similarity";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((module) => ({ default: module.STLViewer })),
);

function comparisonFile(
  model: ModelRead | undefined,
  source: SimilaritySource | undefined,
  hash: string | undefined,
) {
  return model?.files.find(
    (file) =>
      file.file_type !== "gcode" && file.sha256 === hash && (!source || file.id === source.file_id),
  );
}
function extent(file: FileRead | undefined) {
  return Math.max(
    file?.metadata?.bbox_x_mm ?? 0,
    file?.metadata?.bbox_y_mm ?? 0,
    file?.metadata?.bbox_z_mm ?? 0,
  );
}

export function SimilarityComparison({
  candidate,
  models,
}: {
  candidate: SimilarityCandidate;
  models: [ModelRead | undefined, ModelRead | undefined];
}) {
  const { t, locale } = useI18n();
  const [scale, setScale] = useState(false);
  const [mirror, setMirror] = useState(false);
  const [overlay, setOverlay] = useState(false);
  const camera = useMemo(() => createComparisonCamera(), []);
  const observation = candidate.observations?.find(
    (item) => item.lineage_key === candidate.primary_lineage_key,
  );
  const files = [
    comparisonFile(models[0], observation?.source_a, observation?.input_hash_a),
    comparisonFile(models[1], observation?.source_b, observation?.input_hash_b),
  ];
  const referenceSizeMm = Math.max(...files.map(extent), 1);
  // Component transforms are in resource space. Whole-Artifact previews keep their
  // original coordinates; applying a resource transform would misrepresent them.
  const canAlign = observation?.kind === "whole" && candidate.freshness === "current";
  const alignment = canAlign ? candidate.summary.transform : undefined;
  const left = useMemo(() => ({ referenceSizeMm }), [referenceSizeMm]);
  const right = useMemo(
    () => ({ referenceSizeMm, alignment, compensateScale: scale, compensateMirror: mirror }),
    [referenceSizeMm, alignment, scale, mirror],
  );
  const jobsA = useQuery({
    queryKey: ["model-print-jobs", candidate.model_a_id],
    queryFn: () => getModelPrintJobs(candidate.model_a_id),
  });
  const jobsB = useQuery({
    queryKey: ["model-print-jobs", candidate.model_b_id],
    queryFn: () => getModelPrintJobs(candidate.model_b_id),
  });
  const number = (value: number | null | undefined, unit = "") =>
    value == null
      ? t("similarity.missingMeasurement")
      : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value)}${unit}`;
  const sources = [observation?.source_a, observation?.source_b];
  const modelRefs = [candidate.model_a, candidate.model_b];
  const latestPrint = [jobsA, jobsB].map((query) =>
    query.data?.reduce<string | undefined>(
      (latest, job) => (!latest || job.created_at > latest ? job.created_at : latest),
      undefined,
    ),
  );
  const dimensions = (file: FileRead | undefined) =>
    file?.metadata
      ? [file.metadata.bbox_x_mm, file.metadata.bbox_y_mm, file.metadata.bbox_z_mm]
          .map((v) => number(v))
          .join(" × ") + " mm"
      : t("similarity.missingMeasurement");
  const rows = [
    { label: t("similarity.dimensions"), values: files.map(dimensions) },
    {
      label: t("similarity.volume"),
      values: files.map((file, index) =>
        number(sources[index]?.volume ?? file?.metadata?.volume_mm3, " mm³"),
      ),
    },
    {
      label: t("similarity.area"),
      values: sources.map((source) => number(source?.surface_area, " mm²")),
    },
    {
      label: t("similarity.triangles"),
      values: files.map((file, index) =>
        number(sources[index]?.face_count ?? file?.metadata?.triangle_count),
      ),
    },
    {
      label: t("similarity.watertight"),
      values: sources.map((source) =>
        source?.watertight == null
          ? t("similarity.missingMeasurement")
          : source.watertight
            ? t("Yes")
            : t("No"),
      ),
    },
    {
      label: t("similarity.format"),
      values: files.map(
        (file) => file?.file_type.toUpperCase() ?? t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("Size"),
      values: files.map((file) =>
        file ? formatBytes(file.size_bytes) : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("similarity.source"),
      values: files.map((file) =>
        file
          ? t(file.is_external ? "similarity.external" : "similarity.vault")
          : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("Revisions"),
      values: models.map((model) =>
        model
          ? number(model.files.filter((file) => file.file_type === "gcode").length)
          : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("similarity.knownGood"),
      values: models.map((model) =>
        model
          ? t(model.files.some((file) => file.revision_status === "known_good") ? "Yes" : "No")
          : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("similarity.lastPrint"),
      values: latestPrint.map((date) =>
        date ? new Date(date).toLocaleString(locale) : t("similarity.missingMeasurement"),
      ),
    },
  ];
  return (
    <section aria-label={t("similarity.compareTitle")} className="space-y-4">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className="font-medium">{t("similarity.physicalScale")}</span>
        <label className="flex items-center gap-2">
          <Checkbox
            ariaLabel={t("similarity.compensateScale")}
            checked={scale}
            disabled={!alignment}
            onChange={setScale}
          />
          {t("similarity.compensateScale")}
        </label>
        <label className="flex items-center gap-2">
          <Checkbox
            ariaLabel={t("similarity.compensateMirror")}
            checked={mirror}
            disabled={!alignment}
            onChange={setMirror}
          />
          {t("similarity.compensateMirror")}
        </label>
        <label className="flex items-center gap-2">
          <Checkbox
            ariaLabel={t("similarity.overlay")}
            checked={overlay}
            disabled={!files[0] || !files[1]}
            onChange={setOverlay}
          />
          {t("similarity.overlay")}
        </label>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {files.map((file, index) => (
          <div
            key={modelRefs[index].id}
            className="min-w-0 overflow-hidden rounded-lg border border-border"
          >
            <div className="border-b border-border px-4 py-3">
              <Link
                className="break-words font-medium text-primary hover:underline"
                href={`/models/${modelRefs[index].id}`}
              >
                {modelRefs[index].name}
              </Link>
              <p className="truncate text-xs text-muted-foreground">
                {file?.original_filename ?? t("similarity.noPreview")}
              </p>
            </div>
            <div className="h-64 bg-muted/30 sm:h-80">
              {file ? (
                <Suspense
                  fallback={
                    <p role="status" className="p-4 text-sm text-muted-foreground">
                      {t("similarity.loading")}
                    </p>
                  }
                >
                  <STLViewer
                    url={getAssetUrl(`/api/v1/files/${file.id}/stl`)}
                    comparison={index === 0 ? left : right}
                    comparisonCamera={camera}
                    showGrid={false}
                    overlay={
                      index === 0 && overlay && files[1]
                        ? {
                            url: getAssetUrl(`/api/v1/files/${files[1].id}/stl`),
                            comparison: right,
                          }
                        : undefined
                    }
                  />
                </Suspense>
              ) : (
                <p className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
                  {t("similarity.noPreview")}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
      {!canAlign && (
        <p className="text-sm text-muted-foreground">{t("similarity.componentPreviewHelp")}</p>
      )}
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-muted/40">
            <tr>
              <th scope="col" className="p-3">
                {t("similarity.measurement")}
              </th>
              {modelRefs.map((model) => (
                <th scope="col" className="p-3" key={model.id}>
                  {model.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((row) => (
              <tr key={row.label}>
                <th scope="row" className="p-3 font-medium">
                  {row.label}
                </th>
                {row.values.map((value, index) => (
                  <td className="p-3 tabular-nums" key={modelRefs[index].id}>
                    {value}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
