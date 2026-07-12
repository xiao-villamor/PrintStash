"use client";

export function SettingRow({
  label,
  value,
  chip,
  highlight,
  last,
}: {
  label: string;
  value: string;
  chip?: boolean;
  highlight?: boolean;
  last?: boolean;
}) {
  return (
    <div className={`flex justify-between items-center px-3 py-2.5 ${last ? "" : "border-b border-surface-container-high"}`}>
      <span className="font-mono text-xs text-on-surface-variant tracking-wider uppercase">{label}</span>
      {chip ? (
        <span className="px-2 py-0.5 bg-secondary-container text-on-secondary-container rounded font-mono text-2xs">{value}</span>
      ) : (
        <span className={`font-mono text-[13px] ${highlight ? "text-primary font-bold" : "text-on-surface"}`}>{value}</span>
      )}
    </div>
  );
}
