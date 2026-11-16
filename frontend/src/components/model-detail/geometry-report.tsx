import { useQuery } from "@tanstack/react-query";
import { getJson } from "@/lib/api/request";
import { queryKeys } from "@/lib/query-client";
import { derivativeFailureCause, derivativeFailureMessage } from "@/lib/derivative-failures";
import type { DerivativeRead } from "@/types";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { VolumeMeasurement } from "@/types/models";

import { geometryChecks, geometryAnalysis, geometryExplanation } from "@/lib/geometry-checks";

export function GeometryReport({
  volume,
  modelId,
  fileId,
}: {
  volume: VolumeMeasurement | null;
  modelId: number;
  fileId: number | null;
}) {
  useUiLocale();
  const checks = geometryChecks(volume);
  const query = useQuery({
    queryKey: [...queryKeys.model(modelId), "geometry-report", fileId],
    queryFn: ({ signal }) =>
      getJson<DerivativeRead[]>(`/api/v1/files/${fileId}/derivatives`, { signal }),
    enabled: fileId !== null,
    retry: false,
  });
  const derivative = query.data?.find((item) => item.kind === "metadata") ?? null;
  const analysis = geometryAnalysis(derivative);
  const explanation = geometryExplanation(volume);
  const labels = {
    passed: uiText("Passed"),
    warning: uiText("Warning"),
    failed: uiText("Failed"),
    not_checked: uiText("Not checked"),
  };
  return (
    <section className="mt-4 border-t border-border pt-3" aria-label={uiText("Geometry checks")}>
      <h3 className="text-xs font-medium">{uiText("Geometry checks")}</h3>
      <dl className="model-information-facts">
        <dt>{uiText("File analysis")}</dt>
        <dd>{labels[analysis]}</dd>
        <dt>{uiText("Watertightness")}</dt>
        <dd>{labels[checks.watertightness]}</dd>
        <dt>{uiText("Consistent winding")}</dt>
        <dd>{labels[checks.winding]}</dd>
        <dt>{uiText("Volume analysis")}</dt>
        <dd>{labels[checks.volume]}</dd>
      </dl>
      {explanation && <p className="text-xs text-muted-foreground">{uiText(explanation)}</p>}
      {derivative?.failure_reason && (
        <p className="mt-2 text-xs text-muted-foreground">
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
      <p className="mt-2 text-xs text-muted-foreground">
        {uiText(
          "Advisory geometry evidence only. It does not certify that this model or G-code is safe to print. Original files are unchanged.",
        )}
      </p>
    </section>
  );
}
