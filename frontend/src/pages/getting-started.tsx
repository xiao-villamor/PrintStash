import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useRouter } from "@/lib/navigation";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import { prepareSetupStorage, listModelPage, discoverLibraryLocations } from "@/lib/api";
import { SetupFrame } from "@/components/setup-frame";
import { Button } from "@/components/ui/button";
import { UploadModal } from "@/components/upload-modal";
import { ExternalLibrariesPanel } from "@/components/external-libraries-panel";
import type { ModelListItem } from "@/types";

export default function GettingStartedPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();
  const [storage, setStorage] = useState<"checking" | "pending" | "ready">("checking");
  const [upload, setUpload] = useState(false);
  const [folder, setFolder] = useState(false);
  const [locations, setLocations] = useState<string[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState("");
  const [models, setModels] = useState<ModelListItem[]>([]);
  const [modelCount, setModelCount] = useState(0);
  const refresh = useCallback(async () => {
    const page = await listModelPage({ limit: 5 });
    setModels(page.items);
    setModelCount(page.total);
  }, []);
  const handlePrepared = useCallback(async () => {
    setStorage("ready");
    try {
      await refresh();
      setLocations(await discoverLibraryLocations());
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [refresh]);
  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (!user.is_superuser) {
      router.replace("/");
      return;
    }
    void prepareSetupStorage().then(handlePrepared, () => setStorage("pending"));
  }, [loading, user, router, handlePrepared]);
  useEffect(() => {
    if (!folder) return;
    const timer = window.setInterval(() => {
      void refresh().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [folder, refresh]);
  function finish() {
    try {
      localStorage.setItem("printstash.getting-started", "deferred");
    } catch {
      /* Preferences are optional. */
    }
    router.push("/");
  }
  if (!user?.is_superuser) return null;
  return (
    <SetupFrame step={3}>
      <div className="space-y-6">
        {loadError && <p role="alert">{t("setup.failed")}</p>}
        <div>
          <h2 className="text-2xl font-semibold">{t("setup.start")}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t("setup.startHelp")}</p>
        </div>
        {storage !== "ready" ? (
          <div role="status" className="space-y-3">
            <p>{t(storage === "checking" ? "setup.checking" : "setup.pending")}</p>
            {storage === "pending" && (
              <Button
                onClick={() => {
                  setStorage("checking");
                  void prepareSetupStorage().then(handlePrepared, () => setStorage("pending"));
                }}
              >
                {t("setup.retry")}
              </Button>
            )}
          </div>
        ) : (
          <>
            <div className="flex flex-col items-start gap-3">
              <Button onClick={() => setUpload(true)}>{t("setup.upload")}</Button>
              <Button variant="outline" onClick={() => setFolder(true)}>
                {t("setup.connect")}
              </Button>
            </div>
            {folder && (
              <section className="space-y-4">
                <p className="text-sm text-muted-foreground">{t("setup.folderHelp")}</p>
                {locations.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {locations.map((location) => (
                      <Button
                        key={location}
                        variant="outline"
                        onClick={() => setSelectedLocation(location)}
                      >
                        {location}
                      </Button>
                    ))}
                  </div>
                )}
                <ExternalLibrariesPanel
                  key={selectedLocation}
                  canEdit
                  initialRootPath={selectedLocation}
                />
                <details className="rounded-md border border-border p-3">
                  <summary className="cursor-pointer text-sm">{t("setup.mountHelp")}</summary>
                  <p className="mt-3 text-sm text-muted-foreground">
                    {t("setup.mountInstructions")}
                  </p>
                </details>
              </section>
            )}
            {models.length > 0 && (
              <ul
                aria-label={t("setup.modelsReady", { count: String(modelCount) })}
                aria-live="polite"
                className="space-y-2"
              >
                {models.map((model) => (
                  <li key={model.id}>
                    <Link className="text-primary underline" to={`/models/${model.id}`}>
                      {model.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <UploadModal open={upload} onClose={() => setUpload(false)} onUploaded={refresh} />
          </>
        )}
        <div className="flex flex-wrap gap-3 border-t border-border pt-5">
          <Button variant="outline" onClick={finish}>
            {t(models.length ? "setup.library" : "setup.later")}
          </Button>
          <Link
            className="self-center text-sm text-primary underline"
            to="/settings?section=backup"
          >
            {t("setup.backup")}
          </Link>
          <Link className="self-center text-sm text-primary underline" to="/printers">
            {t("setup.printer")}
          </Link>
        </div>
      </div>
    </SetupFrame>
  );
}
