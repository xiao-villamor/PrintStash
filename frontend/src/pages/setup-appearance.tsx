import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { PreviewAppearance } from "@/components/preview-appearance";
import { SetupFrame } from "@/components/setup-frame";
import { appearanceChanged } from "@/lib/preview-appearance";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { chooseSetupSamples } from "@/lib/api/config";
import { captureEditingBase } from "@/lib/api/editing";
import { useAuth } from "@/lib/auth-context";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";
import { useVaultConfigCommand, vaultConfigOptions } from "@/lib/queries/settings-config";
import { getSessionVersion } from "@/lib/session-transport";
import type { PreviewAppearance as Appearance } from "@/types/config";
import type { EditingBase } from "@/types/editing";

export default function SetupAppearancePage() {
  const { t } = useI18n();
  const { user, loading } = useAuth();
  const router = useRouter();
  const config = useQuery({ ...vaultConfigOptions(), enabled: user?.is_superuser === true });
  const command = useVaultConfigCommand();
  const samples = useMutation({ mutationFn: chooseSetupSamples });
  const [includeSamples, setIncludeSamples] = useState(false);
  const busy = command.isPending || samples.isPending;
  const sampleLabel = t("Add the PrintStash sample collection");
  const [draft, setDraft] = useState<{ value: Appearance; base: EditingBase } | null>(null);

  useEffect(() => {
    if (!loading && !user?.is_superuser) router.replace(user ? "/" : "/login");
  }, [loading, router, user]);

  if (!user?.is_superuser) return null;

  async function finish() {
    try {
      if (config.data && draft && appearanceChanged(config.data, draft.value)) {
        await command.mutateAsync({
          session: getSessionVersion(),
          base: draft.base,
          payload: draft.value,
        });
      }
      if (includeSamples) await samples.mutateAsync(true);
      router.replace("/getting-started");
    } catch {
      // Leave the step available so the administrator can retry the choice.
    }
  }

  return (
    <SetupFrame
      actions={
        <>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => router.replace("/getting-started")}
          >
            {t("setup.back")}
          </Button>
          <Button disabled={!config.data} loading={busy} onClick={() => void finish()}>
            {t("setup.saveAppearance")}
          </Button>
        </>
      }
    >
      <h1 className="text-xl font-semibold tracking-tight text-foreground">
        {t("setup.customizePreviews")}
      </h1>
      <p className="mb-4 mt-2 text-sm text-muted-foreground">
        {t("You can change this any time in Settings")}
      </p>
      {config.isPending && <p role="status">{t("Loading…")}</p>}
      {config.isError && (
        <div role="alert" className="flex items-center gap-2 text-sm">
          <p>{t("settings.configLoadFailed")}</p>
          <Button variant="outline" size="sm" onClick={() => void config.refetch()}>
            {t("Retry")}
          </Button>
        </div>
      )}
      {config.data && (
        <PreviewAppearance
          layout="setup"
          value={draft?.value ?? config.data}
          onChange={({ preview_look, preview_floor_grid }) => {
            if (!config.data) return;
            setDraft({
              value: { preview_look, preview_floor_grid },
              base: draft?.base ?? captureEditingBase(config.data),
            });
          }}
          disabled={busy}
        />
      )}
      <label className="mt-4 flex items-center gap-2 border-t border-border pt-3 text-sm">
        <Checkbox
          checked={includeSamples}
          onChange={setIncludeSamples}
          disabled={busy}
          ariaLabel={sampleLabel}
        />
        <span>{sampleLabel}</span>
      </label>
      {(command.isError || samples.isError) && (
        <p role="alert" className="mt-4 text-sm text-destructive">
          {userMessage(samples.error ?? command.error)}
        </p>
      )}
    </SetupFrame>
  );
}
