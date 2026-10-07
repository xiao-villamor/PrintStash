import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { providerAddress, providerLabel } from "@/lib/printer-providers";
import type { PrinterRead } from "@/types";
import type { PrinterEditState } from "./settings-edit";

export function PrinterSettingsReview({
  state,
  canSave,
  original,
  onReview,
  onAdopt,
  onSave,
}: {
  state: PrinterEditState;
  canSave: boolean;
  original: PrinterRead;
  onReview: () => void;
  onAdopt: () => void;
  onSave: () => void;
}) {
  useUiLocale();
  if (state.phase === "idle" || state.phase === "saving") return null;
  const latest = state.phase === "ready" ? state.snapshot : null;
  const connectionChanged =
    latest !== null &&
    (latest.provider !== original.provider ||
      latest.provider_variant !== original.provider_variant ||
      latest.prusalink_auth_mode !== original.prusalink_auth_mode);
  return (
    <div
      role="status"
      aria-label={uiText("Current printer settings")}
      className="space-y-3 border-t border-border p-4"
    >
      <p className="text-sm text-warning">
        {uiText(
          state.problem === "conflict"
            ? "Printer settings changed elsewhere. Review the current values before saving."
            : "The printer save could not be confirmed. Review the current values before trying again.",
        )}
      </p>
      {latest && (
        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt>{uiText("Name")}</dt>
            <dd>{latest.name}</dd>
          </div>
          <div>
            <dt>{uiText("Model")}</dt>
            <dd>{latest.model_name ?? latest.detected_model ?? "—"}</dd>
          </div>
          <div>
            <dt>{uiText("Provider")}</dt>
            <dd>{providerLabel(latest)}</dd>
          </div>
          <div>
            <dt>{uiText("Address")}</dt>
            <dd>{providerAddress(latest)}</dd>
          </div>
          <div>
            <dt>{uiText("Group")}</dt>
            <dd>{latest.group ?? "—"}</dd>
          </div>
          <div>
            <dt>{uiText("Notes")}</dt>
            <dd>{latest.notes ?? "—"}</dd>
          </div>
          <div>
            <dt>{uiText("Printer serial")}</dt>
            <dd>{latest.bambu_serial ?? "—"}</dd>
          </div>
          <div>
            <dt>{uiText("Username")}</dt>
            <dd>{latest.prusalink_username ?? "—"}</dd>
          </div>
          <div>
            <dt>{uiText("Mainboard ID")}</dt>
            <dd>{latest.elegoo_centauri_mainboard_id ?? "—"}</dd>
          </div>
          <div>
            <dt>{uiText("Provider material sync")}</dt>
            <dd>{uiText(latest.provider_material_sync_enabled ? "Enabled" : "Disabled")}</dd>
          </div>
          <div>
            <dt>{uiText("Require operator release")}</dt>
            <dd>{uiText(latest.operator_release_required ? "Enabled" : "Disabled")}</dd>
          </div>
          <div>
            <dt>{uiText("Stored credential")}</dt>
            <dd>
              {uiText(
                {
                  moonraker: latest.has_api_key,
                  bambu_lan: latest.has_bambu_access_code,
                  prusalink:
                    latest.prusalink_auth_mode === "api_key"
                      ? latest.has_prusalink_api_key
                      : latest.has_prusalink_password,
                  elegoo_centauri: latest.has_elegoo_centauri_access_code,
                  octoprint: latest.has_octoprint_api_key,
                }[latest.provider]
                  ? "Configured"
                  : "Not configured",
              )}
            </dd>
          </div>
        </dl>
      )}
      {connectionChanged && (
        <p className="text-sm text-warning">
          {uiText("Adopt the current connection before editing its settings")}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          loading={state.phase === "loading"}
          onClick={onReview}
        >
          {uiText("Review current settings")}
        </Button>
        {latest && (
          <>
            <Button type="button" size="sm" variant="outline" onClick={onAdopt}>
              {uiText("Use current values")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={connectionChanged || !canSave}
              onClick={onSave}
            >
              {uiText("Save revised changes")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
