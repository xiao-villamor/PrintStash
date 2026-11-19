import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PreviewAppearance } from "@/components/preview-appearance";
import { SetupFrame } from "@/components/setup-frame";
import { appearanceChanged } from "@/lib/preview-appearance";
import { Button } from "@/components/ui/button";
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
  const [draft, setDraft] = useState<{ value: Appearance; base: EditingBase } | null>(null);

  useEffect(() => {
    if (!loading && !user?.is_superuser) router.replace(user ? "/" : "/login");
  }, [loading, router, user]);

  if (!user?.is_superuser) return null;

  function finish() {
    router.replace("/getting-started");
  }

  async function saveAndContinue() {
    if (!config.data) return;
    if (draft && appearanceChanged(config.data, draft.value)) {
      try {
        await command.mutateAsync({
          session: getSessionVersion(),
          base: draft.base,
          payload: draft.value,
        });
      } catch {
        return;
      }
    }
    finish();
  }

  return (
    <SetupFrame
      step={4}
      actions={
        <>
          <Button variant="ghost" disabled={command.isPending} onClick={finish}>
            {t("Skip")}
          </Button>
          <Button
            disabled={!config.data || command.isPending}
            onClick={() => void saveAndContinue()}
          >
            {t("setup.next")}
          </Button>
        </>
      }
    >
      <h2 className="text-xl font-semibold text-foreground">{t("Make your library yours")}</h2>
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
          disabled={command.isPending}
        />
      )}
      {command.isError && (
        <p role="alert" className="mt-4 text-sm text-destructive">
          {userMessage(command.error)}
        </p>
      )}
    </SetupFrame>
  );
}
