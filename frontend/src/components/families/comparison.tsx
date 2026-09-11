import { lazy, Suspense, useMemo, useState } from "react";
import { MetadataComparison } from "@/components/metadata-comparison";
import { getAssetUrl } from "@/lib/api/request";
import { createComparisonCamera } from "@/lib/comparison-camera";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { filterValueText } from "@/lib/filter-labels";
import type { FamilyMemberItem } from "@/types/families";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((module) => ({ default: module.STLViewer })),
);

export function FamilyComparison({
  members,
}: {
  members: readonly [FamilyMemberItem, FamilyMemberItem];
}) {
  const { t, locale } = useI18n();
  const camera = useMemo(() => createComparisonCamera(), []);
  const [loadedSizes, setLoadedSizes] = useState<Record<number, number>>({});
  const referenceSizeMm =
    Math.max(
      ...members.flatMap((member) => {
        const actual = member.preview_file && loadedSizes[member.preview_file.id];
        if (actual) return [actual];
        const metadata = member.preview_file?.metadata;
        return [metadata?.bbox_x_mm ?? 0, metadata?.bbox_y_mm ?? 0, metadata?.bbox_z_mm ?? 0];
      }),
    ) || 1;
  const comparison = useMemo(() => ({ referenceSizeMm }), [referenceSizeMm]);
  const number = (value: number | null | undefined) =>
    value == null ? "—" : new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value);
  const dimensions = (member: FamilyMemberItem) => {
    const metadata = member.preview_file?.metadata;
    return [metadata?.bbox_x_mm, metadata?.bbox_y_mm, metadata?.bbox_z_mm].map(number).join(" × ");
  };
  const [left, right] = members;
  const rows: [string, string, string][] = [
    [t("families.role"), t(`families.role.${left.role}`), t(`families.role.${right.role}`)],
    [t("families.scale"), number(left.scale_factor), number(right.scale_factor)],
    [
      t("families.formats"),
      left.formats.join(", ").toUpperCase() || "—",
      right.formats.join(", ").toUpperCase() || "—",
    ],
    [t("families.dimensions"), dimensions(left), dimensions(right)],
    [
      t("families.triangles"),
      number(left.preview_file?.metadata?.triangle_count),
      number(right.preview_file?.metadata?.triangle_count),
    ],
    [
      t("families.volume"),
      number(left.preview_file?.metadata?.volume_mm3),
      number(right.preview_file?.metadata?.volume_mm3),
    ],
    [
      t("families.size"),
      left.preview_file ? formatBytes(left.preview_file.size_bytes) : "—",
      right.preview_file ? formatBytes(right.preview_file.size_bytes) : "—",
    ],
    [t("families.sourceFiles"), number(left.source_file_count), number(right.source_file_count)],
    [
      t("families.revisions"),
      number(left.gcode_revision_count),
      number(right.gcode_revision_count),
    ],
    [t("families.knownGood"), number(left.known_good_count), number(right.known_good_count)],
    [t("families.source"), left.model.source_url || "—", right.model.source_url || "—"],
    [
      t("families.latestPrint"),
      left.latest_print_outcome
        ? filterValueText("print_outcome", left.latest_print_outcome)
        : t("families.noPrint"),
      right.latest_print_outcome
        ? filterValueText("print_outcome", right.latest_print_outcome)
        : t("families.noPrint"),
    ],
    [t("families.note"), left.transformation_note || "—", right.transformation_note || "—"],
  ];
  return (
    <section aria-label={t("families.compare")} className="space-y-3">
      <p className="text-sm font-medium">{t("families.physicalScale")}</p>
      <div className="grid gap-3 md:grid-cols-2">
        {members.map((member) => (
          <div key={member.id} className="min-w-0 overflow-hidden rounded-md border border-border">
            <div className="border-b border-border px-3 py-2">
              <Link
                href={`/models/${member.model_id}`}
                className="break-words text-sm font-medium text-primary hover:underline"
              >
                {member.model.name}
              </Link>
              <p className="truncate text-xs text-muted-foreground">
                {member.preview_file?.original_filename ?? member.formats.join(", ").toUpperCase()}
              </p>
            </div>
            <div className="h-64 bg-muted/30 sm:h-80">
              {member.preview_file ? (
                <Suspense
                  fallback={
                    <p role="status" className="p-4 text-sm text-muted-foreground">
                      {t("families.loading")}
                    </p>
                  }
                >
                  <STLViewer
                    url={getAssetUrl(`/api/v1/files/${member.preview_file.id}/stl`)}
                    comparison={comparison}
                    comparisonCamera={camera}
                    onGeometrySize={(size) => {
                      const id = member.preview_file!.id;
                      setLoadedSizes((current) =>
                        current[id] === size ? current : { ...current, [id]: size },
                      );
                    }}
                    showGrid={false}
                  />
                </Suspense>
              ) : (
                <p className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
                  {t("families.previewUnavailable")}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
      {members.some((member) => member.units === "unknown") && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("families.unitsUnknown")}
        </p>
      )}
      <MetadataComparison headings={[left.model.name, right.model.name]} rows={rows} />
    </section>
  );
}
