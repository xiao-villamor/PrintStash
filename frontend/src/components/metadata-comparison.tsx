import { useI18n } from "@/lib/i18n";

/** A semantic comparison shared by Revision and Model Family views. */
export function MetadataComparison({
  headings,
  rows,
}: {
  headings: readonly [string, string];
  rows: readonly (readonly [string, string, string])[];
}) {
  const { t } = useI18n();
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <table className="w-full table-fixed text-left text-xs">
        <thead className="bg-muted/50">
          <tr>
            <th scope="col" className="w-[30%] px-3 py-2 font-medium text-muted-foreground">
              {t("families.field")}
            </th>
            {headings.map((heading, index) => (
              <th key={index} scope="col" className="break-words px-3 py-2 font-medium">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, left, right]) => (
            <tr key={label} className="border-t border-border">
              <th scope="row" className="break-words px-3 py-2 font-medium text-muted-foreground">
                {label}
              </th>
              <td className="break-words px-3 py-2 font-mono tabular-nums">{left}</td>
              <td
                className={`break-words px-3 py-2 font-mono tabular-nums ${left === right ? "" : "font-semibold text-primary"}`}
              >
                {right}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
