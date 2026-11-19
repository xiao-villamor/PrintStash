import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PreviewAppearance } from "@/components/preview-appearance";
import { appearanceChanged } from "@/lib/preview-appearance";
import { Button } from "@/components/ui/button";
import { captureEditingBase } from "@/lib/api/editing";
import { getPreviewActivity } from "@/lib/api/work";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { previewProgress } from "@/lib/preview-activity";
import { useVaultConfigCommand } from "@/lib/queries/settings-config";
import { getSessionVersion } from "@/lib/session-transport";
import type { PreviewAppearance as Appearance, VaultConfigRead } from "@/types/config";
import type { EditingBase } from "@/types/editing";

const activityKey = ["work", "preview-activity"];

export function PreviewAppearanceSettings({ config }: { config: VaultConfigRead }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const command = useVaultConfigCommand();
  const [draft, setDraft] = useState<{ value: Appearance; base: EditingBase } | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const value = draft?.value ?? config;
  const changed = appearanceChanged(config, value);
  const activity = useQuery({
    queryKey: activityKey,
    queryFn: ({ signal }) => getPreviewActivity({ signal }),
    enabled: savedAt !== null,
    retry: false,
    refetchInterval: (query) => (query.state.data?.remaining ? 4000 : false),
  });
  const progress = savedAt !== null && activity.dataUpdatedAt >= savedAt ? activity.data : null;
  const finished = progress?.remaining === 0;

  async function save() {
    if (!draft || !changed) return;
    try {
      await command.mutateAsync({
        session: getSessionVersion(),
        base: draft.base,
        payload: draft.value,
      });
      setDraft(null);
      setSavedAt(Date.now());
      void client.invalidateQueries({ queryKey: activityKey });
    } catch {
      // The command owns the error and the draft stays available.
    }
  }

  return (
    <div className="mt-5 border-t border-border pt-5">
      <PreviewAppearance
        value={value}
        onChange={(next) => {
          const appearance = {
            preview_look: next.preview_look,
            preview_floor_grid: next.preview_floor_grid,
          };
          setDraft(
            appearanceChanged(config, appearance)
              ? { value: appearance, base: draft?.base ?? captureEditingBase(config) }
              : null,
          );
        }}
        disabled={command.isPending}
        footer={
          <>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                disabled={!changed || command.isPending}
                onClick={() => void save()}
              >
                {t("Save")}
              </Button>
              {savedAt !== null && (
                <div role="status" className="text-xs text-muted-foreground">
                  <p>
                    {t(
                      finished
                        ? "Cover rendering finished"
                        : "Covers are being re-rendered in the background",
                    )}
                  </p>
                  {progress && progress.total > 0 && <p>{previewProgress(progress).detail}</p>}
                </div>
              )}
            </div>
            {command.isError && (
              <p role="alert" className="text-sm text-destructive">
                {userMessage(command.error)}
              </p>
            )}
          </>
        }
      />
    </div>
  );
}
