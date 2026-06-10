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
    <div className={`flex justify-between items-center px-3 py-2.5 ${last ? "" : "border-b border-[var(--surface-container-high)]"}`}>
      <span className="font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase">{label}</span>
      {chip ? (
        <span className="px-2 py-0.5 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] rounded font-mono text-[11px]">{value}</span>
      ) : (
        <span className={`font-mono text-[13px] ${highlight ? "text-[var(--primary)] font-bold" : "text-[var(--on-surface)]"}`}>{value}</span>
      )}
    </div>
  );
}
