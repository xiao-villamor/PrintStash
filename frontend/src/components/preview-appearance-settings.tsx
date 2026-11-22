import { useEffect, useEffectEvent, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Palette } from "lucide-react";
import { PreviewAppearance } from "@/components/preview-appearance";
import { SettingsCard } from "@/components/settings-card";
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
const CHANGE_DELAY_MS = 250;

type AppearanceEdit = { value: Appearance; base: EditingBase };

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

  const apply = useEffectEvent(async (edit: AppearanceEdit) => {
    if (edit !== draft || disabled || command.isPending) return;
    if (!appearanceChanged(config, edit.value)) {
      setDraft(null);
      return;
    }
    const session = getSessionVersion();
    try {
      const receipt = await command.mutateAsync({ session, base: edit.base, payload: edit.value });
      setDraft((latest) =>
        latest && latest !== edit ? { ...latest, base: captureEditingBase(receipt) } : null,
      );
      void client.invalidateQueries({ queryKey: activityKey });
    } catch {
      if (getSessionVersion() !== session) return;
      setDraft(null);
      void client.invalidateQueries({ queryKey: queryKeys.vaultConfig, exact: true });
    }
  });

  // Let keyboard and pointer bursts settle before advancing the render generation.
  useEffect(() => {
    if (!draft || command.isPending || disabled) return;
    const timeout = setTimeout(() => void apply(draft), CHANGE_DELAY_MS);
    return () => clearTimeout(timeout);
  }, [draft, command.isPending, disabled]);

  return (
    <SettingsCard
      icon={Palette}
      className="settings-preview-look"
      title={t("Preview look")}
      description={t(
        "Colour and floor of generated Model images. Changes re-render existing images in the background.",
      )}
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
        disabled={disabled}
        status={
          <>
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
