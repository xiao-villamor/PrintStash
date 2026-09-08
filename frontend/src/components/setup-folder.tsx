import { useState } from "react";
import { FolderOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";
import {
  createExternalLibrary,
  getVaultConfig,
  scanExternalLibrary,
  updateVaultConfig,
} from "@/lib/api";
import { userMessage } from "@/lib/errors";
import { trackImportJob, waitForImportJob } from "@/lib/task-center";
import { uiMessage } from "@/lib/locale";
import type { ExternalLibrary } from "@/types";

/** A first mounted source uses the same source and ingestion contracts as Settings. */
export function SetupFolder({
  locations,
  onIndexed,
  onBusyChange,
}: {
  locations: string[];
  onIndexed: (complete: boolean) => Promise<number | null>;
  onBusyChange: (busy: boolean) => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [source, setSource] = useState<ExternalLibrary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [empty, setEmpty] = useState(false);
  async function connect() {
    setBusy(true);
    onBusyChange(true);
    setError("");
    setEmpty(false);
    try {
      // Selecting a suggested folder never enables sources. Only this explicit
      // submit enables the feature and creates the user-confirmed source.
      let current = source;
      if (!current) {
        const config = await getVaultConfig();
        if (!config.external_libraries_enabled) {
          await updateVaultConfig({ external_libraries_enabled: true });
        }
        current = await createExternalLibrary({
          name: name.trim(),
          root_path: path.trim(),
          scan_schedule: "0 * * * *",
          watch_mode: "auto",
          collection_mode: "mirror",
        });
        setSource(current);
      }
      const job = await scanExternalLibrary(current.id);
      trackImportJob(job.job_id, uiMessage("Scan {value1}", { value1: current.name }));
      const result = await waitForImportJob(job.job_id);
      if (result.state === "failed") throw new Error(result.error || "scan_failed");
      const complete = result.completion !== "partial";
      const count = await onIndexed(complete);
      if (!complete) setError(t("setup.scanPartial"));
      setEmpty(count === 0);
    } catch (cause) {
      setError(userMessage(cause));
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }
  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        void connect();
      }}
    >
      <div className="space-y-2 text-sm leading-relaxed text-muted-foreground">
        <p>{t("setup.folderIntro")}</p>
        <p>{t("setup.folderHelp")}</p>
      </div>
      <fieldset disabled={busy || source !== null} className="space-y-4">
        {locations.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">
              {t("setup.availableFolders")}
            </p>
            <div className="flex flex-wrap gap-2">
              {locations.map((location) => (
                <Button
                  key={location}
                  type="button"
                  variant="outline"
                  className="h-auto min-h-11 max-w-full whitespace-normal break-all"
                  onClick={() => {
                    setPath(location);
                    setError("");
                  }}
                >
                  <FolderOpen className="h-4 w-4 shrink-0" aria-hidden />
                  {location}
                </Button>
              ))}
            </div>
          </div>
        )}
        <div className="space-y-2">
          <label htmlFor="setup-source-name" className="text-sm font-medium">
            {t("setup.folderName")}
          </label>
          <Input
            id="setup-source-name"
            required
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setError("");
            }}
            placeholder={t("setup.folderNameExample")}
            className="h-11"
          />
        </div>
        <div className="space-y-2">
          <label htmlFor="setup-source-path" className="text-sm font-medium">
            {t("setup.folderPath")}
          </label>
          <Input
            id="setup-source-path"
            required
            value={path}
            onChange={(event) => {
              setPath(event.target.value);
              setError("");
            }}
            placeholder={t("setup.folderPathExample")}
            className="h-11"
            aria-describedby="setup-source-path-help"
          />
          <p id="setup-source-path-help" className="text-xs leading-relaxed text-muted-foreground">
            {t("setup.folderPathHelp")}
          </p>
        </div>
      </fieldset>
      <details className="text-sm">
        <summary className="cursor-pointer py-2 text-muted-foreground">
          {t("setup.mountHelp")}
        </summary>
        <p className="mt-2 break-words text-sm leading-relaxed text-muted-foreground">
          {t("setup.mountInstructions")}
        </p>
      </details>
      {error && (
        <div role="alert" className="space-y-1 rounded-md border border-destructive/30 p-3 text-sm">
          <p className="font-medium">{t(source ? "setup.scanFailed" : "setup.folderFailed")}</p>
          <p className="text-muted-foreground">{error}</p>
        </div>
      )}
      {empty && (
        <p role="status" className="rounded-md bg-muted p-3 text-sm leading-relaxed">
          {t("setup.scanEmpty")}
        </p>
      )}
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("setup.scanning")}
        </p>
      )}
      <Button
        type="submit"
        disabled={busy || (!source && (!name.trim() || !path.trim()))}
        className="h-11 w-full"
      >
        {t(busy ? "setup.scanning" : source ? "setup.scanAgain" : "setup.connectScan")}
      </Button>
      <p className="text-xs leading-relaxed text-muted-foreground">{t("setup.folderDefaults")}</p>
    </form>
  );
}
