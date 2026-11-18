import { SettingsGroupHead } from "@/components/settings-card";
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
  return (
    <section
      className="settings-flat overflow-hidden border border-border bg-card"
      aria-label={uiText("Import readiness")}
    >
      <SettingsGroupHead
        title={uiText("Import readiness")}
        description={uiText(
          "Pairing, provider configuration and a tested download are different checks.",
        )}
      />
      <dl className="grid grid-cols-2 gap-2 py-3 text-sm">
        <dt>{uiText("Vault connection")}</dt>
        <dd>{uiText("Responded at the last check")}</dd>
        <dt>{uiText("Paired browsers")}</dt>
        <dd>{active.length}</dd>
        <dt>{uiText("Configured providers")}</dt>
        <dd>
          {connections
            .filter((connection) => connection.connected)
            .map((connection) => connection.provider)
            .join(", ") || uiText("None")}
        </dd>
        <dt>{uiText("Marketplace session and download")}</dt>
        <dd>{uiText("Not checked")}</dd>
        <dt>{uiText("Staging capacity")}</dt>
        <dd>{uiText("Checked when reserving an upload")}</dd>
      </dl>
      <p className="pb-3 text-xs text-muted-foreground">
        {uiText("Status read at")}{" "}
        <time dateTime={new Date(checkedAt).toISOString()}>
          {new Date(checkedAt).toLocaleString()}
        </time>
      </p>
      {user?.is_superuser && (
        <div className="flex flex-wrap gap-3 border-t border-border py-3 text-xs">
          <Link className="underline" href="/settings?section=storage">
            {uiText("Storage")}
          </Link>
          <Link className="underline" href="/settings?section=work">
            {uiText("Background work")}
          </Link>
          <Link className="underline" href="/settings?section=backup">
            {uiText("Backups")}
          </Link>
          <Link className="underline" href="/settings?section=maintenance">
            {uiText("Audits")}
          </Link>
          <p>
            {uiText(
              "Check verification dates. An existing backup file is not proof of a verified backup.",
            )}
          </p>
        </div>
      )}
    </section>
  );
}
