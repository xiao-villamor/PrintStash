import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Box, Check, FolderOpen, Loader2, Upload } from "lucide-react";
import { Link } from "react-router-dom";
import { useRouter } from "@/lib/navigation";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import { prepareSetupStorage, listModelPage, discoverLibraryLocations } from "@/lib/api";
import { listTasks, subscribeTasks, taskDetail, type TaskItem } from "@/lib/task-center";
import { SetupFrame } from "@/components/setup-frame";
import { SetupFolder } from "@/components/setup-folder";
import { Button } from "@/components/ui/button";
import { UploadModal } from "@/components/upload-modal";
import type { ModelListItem } from "@/types";

export default function GettingStartedPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();
  const heading = useRef<HTMLHeadingElement>(null);
  const [storage, setStorage] = useState<"checking" | "pending" | "ready">("checking");
  const [upload, setUpload] = useState(false);
  const [folder, setFolder] = useState(false);
  const [folderBusy, setFolderBusy] = useState(false);
  const [locations, setLocations] = useState<string[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [models, setModels] = useState<ModelListItem[]>([]);
  const [modelCount, setModelCount] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<TaskItem | undefined>();
  const refresh = useCallback(async () => {
    setCatalogBusy(true);
    try {
      const page = await listModelPage({ limit: 5 });
      setModels(page.items);
      setModelCount(page.total);
      setLoadError(false);
      return page.total;
    } catch {
      setLoadError(true);
      return null;
    } finally {
      setCatalogBusy(false);
    }
  }, []);
  const handlePrepared = useCallback(async () => {
    setStorage("ready");
    await Promise.all([
      refresh(),
      discoverLibraryLocations().then(setLocations, () => setLocations([])),
    ]);
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
    if (!taskId) return;
    const update = () => setTask(listTasks().find((item) => item.id === taskId));
    update();
    return subscribeTasks(update);
  }, [taskId]);
  useEffect(() => {
    heading.current?.focus();
  }, [folder]);
  function finish() {
    try {
      localStorage.setItem("printstash.getting-started", "deferred");
    } catch {
      /* Optional preference. */
    }
    router.push("/");
  }
  const uploading = task?.status === "running" || task?.status === "pending";
  if (!user?.is_superuser) return null;
  return (
    <SetupFrame step={3}>
      <div className="space-y-6">
        {folder && (
          <Button
            variant="ghost"
            className="-ml-3 h-11"
            disabled={folderBusy}
            onClick={() => setFolder(false)}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
            {t("setup.backChoices")}
          </Button>
        )}
        <div className="space-y-3">
          {!folder && storage === "ready" && (
            <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Check className="h-4 w-4 text-success" aria-hidden />
              {t("setup.accountReady")}
            </p>
          )}
          <h2
            ref={heading}
            aria-live="polite"
            tabIndex={-1}
            className="text-2xl font-bold tracking-tight outline-none sm:text-3xl"
          >
            {t(
              folder ? "setup.connect" : models.length ? "setup.firstSuccess" : "setup.firstTitle",
            )}
          </h2>
          {!folder && (
            <p className="max-w-lg text-sm leading-relaxed text-muted-foreground">
              {t(models.length ? "setup.successHelp" : "setup.startHelp")}
            </p>
          )}
        </div>
        {loadError && (
          <div
            role="alert"
            className="space-y-3 rounded-md border border-destructive/30 p-4 text-sm"
          >
            <p>{t("setup.catalogFailed")}</p>
            <Button variant="outline" disabled={catalogBusy} onClick={() => void refresh()}>
              {t("setup.retry")}
            </Button>
          </div>
        )}
        {storage !== "ready" ? (
          <div role="status" className="space-y-3 rounded-md bg-muted p-4 text-sm leading-relaxed">
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
            <div hidden={!folder}>
              <SetupFolder
                locations={locations}
                onBusyChange={setFolderBusy}
                onIndexed={async (complete) => {
                  const count = await refresh();
                  if (complete && count !== null && count > 0) setFolder(false);
                  return count;
                }}
              />
            </div>
            {!folder && (
              <>
                {models.length > 0 ? (
                  <div className="space-y-4">
                    <ul
                      aria-label={t("setup.modelsReady", { count: String(modelCount) })}
                      className="divide-y divide-border rounded-md border border-border"
                    >
                      {models.map((model) => (
                        <li key={model.id}>
                          <Link
                            className="flex min-h-16 items-center gap-3 px-4 py-3 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            to={`/models/${model.id}`}
                          >
                            <Box className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                            <span className="min-w-0 flex-1 break-words text-sm font-medium">
                              {model.name}
                            </span>
                            <ArrowRight
                              className="h-4 w-4 shrink-0 text-muted-foreground"
                              aria-hidden
                            />
                          </Link>
                        </li>
                      ))}
                    </ul>
                    <Button className="h-11 w-full" onClick={finish}>
                      {t("setup.library")}
                      <ArrowRight className="h-4 w-4" aria-hidden />
                    </Button>
                    <div className="flex flex-wrap gap-2">
                      <Button variant="ghost" disabled={uploading} onClick={() => setUpload(true)}>
                        {t("setup.addAnother")}
                      </Button>
                      <Button variant="ghost" disabled={uploading} onClick={() => setFolder(true)}>
                        {t("setup.connect")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-5">
                    <section className="space-y-4 rounded-lg border border-outline-variant bg-surface-container p-5 sm:p-6">
                      <div className="flex items-start gap-3">
                        <Upload
                          className="mt-1 h-5 w-5 shrink-0 text-muted-foreground"
                          aria-hidden
                        />
                        <div>
                          <h3 className="text-base font-semibold">{t("setup.uploadTitle")}</h3>
                          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                            {t("setup.uploadHelp")}
                          </p>
                        </div>
                      </div>
                      <Button
                        className="h-11 w-full"
                        disabled={uploading}
                        onClick={() => setUpload(true)}
                      >
                        {t("setup.upload")}
                        <ArrowRight className="h-4 w-4" aria-hidden />
                      </Button>
                      <p className="text-xs text-muted-foreground">{t("setup.fileFormats")}</p>
                    </section>
                    <Button
                      variant="ghost"
                      disabled={uploading}
                      className="h-auto min-h-16 w-full justify-start gap-3 whitespace-normal px-2 text-left"
                      onClick={() => setFolder(true)}
                    >
                      <FolderOpen className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{t("setup.connect")}</span>
                        <span className="mt-1 block text-xs font-normal leading-relaxed text-muted-foreground">
                          {t("setup.connectHelp")}
                        </span>
                      </span>
                      <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    </Button>
                  </div>
                )}
                {uploading && (
                  <p
                    role="status"
                    className="flex items-center gap-2 rounded-md bg-muted p-4 text-sm"
                  >
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
                    {t("setup.uploading")}
                  </p>
                )}
                {task?.status === "failed" && (
                  <div
                    role="alert"
                    className="space-y-3 rounded-md border border-destructive/30 p-4 text-sm"
                  >
                    <p className="font-medium">{t("setup.uploadFailed")}</p>
                    <p className="text-muted-foreground">{taskDetail(task)}</p>
                    <Button variant="outline" onClick={() => setUpload(true)}>
                      {t("setup.chooseAnother")}
                    </Button>
                  </div>
                )}
              </>
            )}
            <UploadModal
              open={upload}
              onboarding
              onClose={() => setUpload(false)}
              onTaskStarted={setTaskId}
              onUploaded={async () => {
                await refresh();
              }}
            />
          </>
        )}
        {!folder && (
          <footer className="border-t border-border pt-5">
            {models.length ? (
              <details>
                <summary className="cursor-pointer py-2 text-sm text-muted-foreground">
                  {t("setup.nextOptional")}
                </summary>
                <div className="mt-3 flex flex-col items-start gap-4 text-sm">
                  <Link
                    className="text-primary underline underline-offset-4"
                    to="/settings?section=backup"
                  >
                    {t("setup.backup")}
                  </Link>
                  <Link className="text-primary underline underline-offset-4" to="/printers">
                    {t("setup.printer")}
                  </Link>
                </div>
              </details>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="max-w-xs text-xs leading-relaxed text-muted-foreground">
                  {t("setup.resumeHelp")}
                </p>
                <Button variant="ghost" className="h-11" onClick={finish}>
                  {t("setup.later")}
                </Button>
              </div>
            )}
          </footer>
        )}
      </div>
    </SetupFrame>
  );
}
