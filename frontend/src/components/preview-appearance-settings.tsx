import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Palette } from "lucide-react";
import { PreviewAppearance } from "@/components/preview-appearance";
import { SettingsCard } from "@/components/settings-card";
import { Button } from "@/components/ui/button";
import { captureEditingBase } from "@/lib/api/editing";
import { getPreviewActivity } from "@/lib/api/work";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { appearanceChanged } from "@/lib/preview-appearance";
import { useVaultConfigCommand } from "@/lib/queries/settings-config";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion } from "@/lib/session-transport";
import type { PreviewAppearance as Appearance, VaultConfigRead } from "@/types/config";
import type { EditingBase } from "@/types/editing";

const activityKey = ["work", "preview-activity"];

type AppearanceEdit = { value: Appearance; base: EditingBase };

/**
 * Trying a colour or the floor grid only changes the sample cards below.
 * Existing images re-render once, when the administrator applies the look.
 */
export function PreviewAppearanceSettings({
  config,
  disabled,
}: {
  config: VaultConfigRead;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const command = useVaultConfigCommand();
  const [draft, setDraft] = useState<AppearanceEdit | null>(null);
  const activity = useQuery({
    queryKey: activityKey,
    queryFn: ({ signal }) => getPreviewActivity({ signal }),
    retry: false,
    refetchInterval: (query) => (query.state.data?.remaining ? 4000 : false),
  });
  const pending = draft !== null && appearanceChanged(config, draft.value);

  async function apply() {
    if (!draft || !pending || disabled || command.isPending) return;
    const session = getSessionVersion();
    try {
      await command.mutateAsync({ session, base: draft.base, payload: draft.value });
      setDraft(null);
      void client.invalidateQueries({ queryKey: activityKey });
      void client.invalidateQueries({ queryKey: queryKeys.previewLook, exact: true });
    } catch {
      if (getSessionVersion() !== session) return;
      void client.invalidateQueries({ queryKey: queryKeys.vaultConfig, exact: true });
    }
  }

  return (
    <SettingsCard
      icon={Palette}
      className="settings-preview-look"
      title={t("Preview look")}
      description={t(
        "Colour and floor of generated Model images. Try a look in the preview; applying it re-renders existing images in the background.",
      )}
      footer={
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setDraft(null)}
            disabled={!pending || command.isPending}
          >
            {t("Discard")}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void apply()}
            loading={command.isPending}
            disabled={!pending || disabled}
          >
            {t("Apply look")}
          </Button>
        </div>
      }
    >
      <PreviewAppearance
        layout="settings"
        value={draft?.value ?? config}
        onChange={({ preview_look, preview_floor_grid }) =>
          setDraft((latest) => ({
            base: latest?.base ?? captureEditingBase(config),
            value: { preview_look, preview_floor_grid },
          }))
        }
        disabled={disabled || command.isPending}
        status={
          <>
            {pending && (
              <p role="status" className="text-muted-foreground">
                {t("Not applied yet. Existing images keep their current look.")}
              </p>
            )}
            {activity.data && activity.data.remaining > 0 && (
              <p role="status" className="text-muted-foreground">
                {t("Re-rendering images · {remaining} remaining", {
                  remaining: activity.data.remaining,
                })}
              </p>
            )}
            {command.isError && (
              <p role="alert" className="text-destructive">
                {userMessage(command.error)}
              </p>
            )}
          </>
        }
      />
    </SettingsCard>
  );
}
