import { useQuery } from "@tanstack/react-query";
import { getJson } from "@/lib/api/request";
import {
  derivativeEvidenceKey,
  useDerivativeEvidenceUpdates,
} from "@/lib/queries/derivative-evidence";
import { derivativeFailureCause, derivativeFailureMessage } from "@/lib/derivative-failures";
import type { DerivativeRead, FileRead } from "@/types";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { geometryChecks, geometryAnalysis, geometryExplanation } from "@/lib/geometry-checks";
import { PropertyRow } from "./inspector-section";
import { meshGeometryFacts, type useMeshGeometryMeasurement } from "./use-mesh-geometry-facts";

/**
 * Measured geometry and advisory checks of one source file, as fact rows for
 * its card. Evidence only: it never certifies that a file is safe to print.
 */
export function GeometryFacts({
  modelId,
  sourceFile,
  measurement = "unavailable",
}: {
  modelId: number;
  sourceFile: FileRead;
  measurement?: ReturnType<typeof useMeshGeometryMeasurement>;
}) {
  useUiLocale();
  const volume = sourceFile.metadata?.volume_measurement ?? null;
  const fileId = sourceFile.file_type === "gcode" ? null : sourceFile.id;
  const geometry = meshGeometryFacts(sourceFile, measurement);
  const checks = geometryChecks(volume);
  const query = useQuery({
    queryKey: derivativeEvidenceKey(fileId, "metadata"),
    queryFn: ({ signal }) =>
      getJson<DerivativeRead[]>(`/api/v1/files/${fileId}/derivatives`, { signal }),
    enabled: fileId !== null,
    retry: false,
  });
  useDerivativeEvidenceUpdates(modelId, fileId, "metadata");
  const derivative = query.data?.find((item) => item.kind === "metadata") ?? null;
  const analysis = geometryAnalysis(derivative);
  const explanation = geometryExplanation(volume);
  const labels = {
    passed: uiText("Passed"),
    warning: uiText("Warning"),
    failed: uiText("Failed"),
    not_checked: uiText("Not checked"),
  };
  const results: [string, keyof typeof labels][] = [
    [uiText("File analysis"), analysis],
    [uiText("Watertightness"), checks.watertightness],
    [uiText("Consistent winding"), checks.winding],
    [uiText("Volume analysis"), checks.volume],
  ];
  return (
    <>
      {geometry.emptyMessage ? (
        <PropertyRow label={uiText("Geometry")}>
          <span className="text-muted-foreground">{geometry.emptyMessage}</span>
        </PropertyRow>
      ) : (
        geometry.facts.map(([label, value]) => (
          <PropertyRow key={label} label={label}>
            {value}
          </PropertyRow>
        ))
      )}
      <PropertyRow label={uiText("Geometry checks")}>
        <div className="min-w-0 space-y-1">
          <p className="flex flex-wrap gap-x-3 gap-y-0.5">
            {results.map(([label, state]) => (
              <span key={label}>
                <span className="text-muted-foreground">{label}</span> {labels[state]}
              </span>
            ))}
          </p>
          {explanation && <p className="text-xs text-muted-foreground">{uiText(explanation)}</p>}
          {derivative?.failure_reason && (
            <p className="text-xs text-muted-foreground">
              {analysis === "not_checked"
                ? uiText(
                    "Analysis was not completed because this format, feature or resource requirement is not supported.",
                  )
                : derivativeFailureMessage(derivativeFailureCause(derivative.failure_reason))}
            </p>
          )}
          {query.isError && (
            <p className="text-xs text-muted-foreground">
              {uiText("Analysis status could not be loaded.")}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {uiText(
              "Advisory geometry evidence only. It does not certify that this model or G-code is safe to print. Original files are unchanged.",
            )}
          </p>
        </div>
      </PropertyRow>
    </>
  );
}
