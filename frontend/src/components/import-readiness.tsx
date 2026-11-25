import { SettingsCard } from "@/components/settings-card";
import { useAuth } from "@/lib/auth-context";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { BrowserDeviceRead, ProviderConnectionRead } from "@/types";

/** Passive summary: pairing and configuration cannot prove a marketplace download. */
export function ImportReadiness({
  devices,
  connections,
  checkedAt,
}: {
  devices: BrowserDeviceRead[];
  connections: ProviderConnectionRead[];
  checkedAt: number;
}) {
  useUiLocale();
  const { user } = useAuth();
  const active = devices.filter((device) => device.revoked_at === null);
  const connected = connections
    .filter((connection) => connection.connected)
    .map((connection) => connection.provider)
    .join(", ");
  const rows: { label: string; hint?: string; value: string; tone: "ok" | "idle" }[] = [
    {
      label: uiText("Vault connection"),
      hint: uiText("Responded at the last check"),
      value: uiText("Connected"),
      tone: "ok",
    },
    {
      label: uiText("Paired browsers"),
      value: String(active.length),
      tone: active.length > 0 ? "ok" : "idle",
    },
    {
      label: uiText("Configured providers"),
      value: connected || uiText("None"),
      tone: connected ? "ok" : "idle",
    },
    {
      label: uiText("Marketplace session and download"),
      value: uiText("Not checked"),
      tone: "idle",
    },
    {
      label: uiText("Staging capacity"),
      hint: uiText("Checked when reserving an upload"),
      value: uiText("Not checked"),
      tone: "idle",
    },
  ];
  return (
    <SettingsCard
      title={uiText("Import readiness")}
      description={uiText(
        "Pairing, provider configuration and a tested download are different checks.",
      )}
    >
      <dl>
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex items-center justify-between gap-6 border-b border-border py-3 text-sm last:border-b-0"
          >
            <dt className="min-w-0">
              <span className="block font-medium text-foreground">{row.label}</span>
              {row.hint && <span className="block text-sm text-muted-foreground">{row.hint}</span>}
            </dt>
            <dd className="flex shrink-0 items-center gap-2">
              <span
                aria-hidden
                className={
                  row.tone === "ok"
                    ? "size-1.5 rounded-full bg-success"
                    : "size-1.5 rounded-full bg-muted-foreground/50"
                }
              />
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border py-3 text-sm text-muted-foreground">
        <span>
          {uiText("Status read at")}{" "}
          <time dateTime={new Date(checkedAt).toISOString()}>
            {new Date(checkedAt).toLocaleString()}
          </time>
        </span>
        {user?.is_superuser && (
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <Link className="text-primary hover:underline" href="/settings?section=storage">
              {uiText("Storage")}
            </Link>
            <Link className="text-primary hover:underline" href="/settings?section=work">
              {uiText("Background work")}
            </Link>
            <Link className="text-primary hover:underline" href="/settings?section=backup">
              {uiText("Backups")}
            </Link>
            <Link className="text-primary hover:underline" href="/settings?section=maintenance">
              {uiText("Audits")}
            </Link>
          </span>
        )}
      </div>
      {user?.is_superuser && (
        <p className="border-t border-border py-3 text-sm text-muted-foreground">
          {uiText(
            "Check verification dates. An existing backup file is not proof of a verified backup.",
          )}
        </p>
      )}
    </SettingsCard>
  );
}
