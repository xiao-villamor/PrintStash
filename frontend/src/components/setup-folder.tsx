import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";
import { vaultConfigOptions, useVaultConfigCommand } from "@/lib/queries/settings-config";
import {
  librarySourcesOptions,
  useLibrarySourceCommand,
} from "@/lib/queries/settings-library-sources";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { captureEditingBase } from "@/lib/api/editing";
import { onAuthChange } from "@/lib/auth-store";
import type { VaultConfigRead } from "@/types";
import { parseApiError, userMessage } from "@/lib/errors";
import { uiMessage, uiText } from "@/lib/locale";

type ActivationReview =
  | { phase: "idle" }
  | { phase: "required" | "loading"; problem: "conflict" | "unconfirmed" }
  | { phase: "ready"; problem: "conflict" | "unconfirmed"; snapshot: VaultConfigRead };

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
  const [source, setSource] = useState<{ id: number; name: string } | null>(null);
  const [session] = useState(getSessionVersion);
  const [retired, setRetired] = useState(false);
  const [review, setReview] = useState<ActivationReview>({ phase: "idle" });
  const live = useRef(true);
  const pending = useRef(false);
  const config = useQuery({ ...vaultConfigOptions(), enabled: false });
  const sources = useQuery({ ...librarySourcesOptions(), enabled: false });
  const configCommand = useVaultConfigCommand();
  const sourceCommand = useLibrarySourceCommand();
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      setRetired(true);
      setName("");
      setPath("");
      setSource(null);
      setReview({ phase: "idle" });
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  function assertCurrent() {
    requireSessionVersion(session);
    if (!live.current) throw new DOMException("First-folder view was disposed", "AbortError");
  }
  function isCurrent() {
    return live.current && session === getSessionVersion();
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [empty, setEmpty] = useState(false);
  async function connect(reviewed?: VaultConfigRead) {
    if (!isCurrent() || pending.current || (review.phase !== "idle" && !reviewed)) return;
    pending.current = true;
    setBusy(true);
    onBusyChange(true);
    setError("");
    setEmpty(false);
    try {
      // Selecting a suggested folder never enables sources. Only this explicit
      // submit enables the feature and creates the user-confirmed source.
      let current = source;
      if (!current) {
        const snapshot = reviewed ?? (await config.refetch({ throwOnError: true })).data;
        assertCurrent();
        if (!snapshot) throw new Error("Configuration receipt is missing");
        if (!snapshot.external_libraries_enabled) {
          try {
            await configCommand.mutateAsync({
              session,
              payload: { external_libraries_enabled: true },
              base: captureEditingBase(snapshot),
            });
          } catch (cause) {
            const status = parseApiError(cause).status;
            if (isCurrent() && (status === 412 || status === 428 || status === 0 || status >= 500))
              setReview({
                phase: "required",
                problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
              });
            throw cause;
          }
          assertCurrent();
        }
        setReview({ phase: "idle" });
        await sources.refetch({ throwOnError: true });
        assertCurrent();
        const created = await sourceCommand.mutateAsync({
          kind: "create",
          session,
          payload: {
            name: name.trim(),
            root_path: path.trim(),
            scan_schedule: "0 * * * *",
            watch_mode: "auto",
            collection_mode: "mirror",
          },
        });
        assertCurrent();
        current = { id: created.id, name: created.name };
        setSource(current);
      }
      const result = await sourceCommand.mutateAsync({
        kind: "scan",
        session,
        id: current.id,
        title: uiMessage("Scan {value1}", { value1: current.name }),
      });
      assertCurrent();
      const complete = result.completion !== "partial";
      const count = await onIndexed(complete);
      assertCurrent();
      if (!complete) setError(t("setup.scanPartial"));
      setEmpty(count === 0);
    } catch (cause) {
      if (isCurrent()) setError(userMessage(cause));
    } finally {
      pending.current = false;
      if (isCurrent()) {
        setBusy(false);
        onBusyChange(false);
      }
    }
  }
  async function reviewActivation() {
    if (!isCurrent() || pending.current || review.phase === "idle" || review.phase === "loading")
      return;
    const problem = review.problem;
    setReview({ phase: "loading", problem });
    setError("");
    try {
      const result = await config.refetch({ throwOnError: true });
      assertCurrent();
      if (!result.data) throw new Error("Configuration review has no data");
      setReview({ phase: "ready", problem, snapshot: result.data });
    } catch (cause) {
      if (isCurrent()) {
        setReview({ phase: "required", problem });
        setError(userMessage(cause));
      }
    }
  }
  if (retired) return null;
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
      {review.phase !== "idle" && (
        <div role="alert" className="space-y-3 text-sm">
          <p>
            {uiText(
              review.problem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed",
            )}
          </p>
          <Button
            type="button"
            variant="outline"
            disabled={busy || review.phase === "loading"}
            onClick={() => void reviewActivation()}
          >
            {uiText("library.reviewLatest")}
          </Button>
          {review.phase === "ready" && (
            <section aria-label={uiText("library.latestVersion")} className="space-y-3">
              <h3 className="font-semibold">{uiText("library.latestVersion")}</h3>
              <p>
                {uiText("Library sources")}:{" "}
                {uiText(review.snapshot.external_libraries_enabled ? "Enabled" : "Disabled")}
              </p>
              <Button
                type="button"
                disabled={busy || !name.trim() || !path.trim()}
                onClick={() => void connect(review.snapshot)}
              >
                {uiText("library.retryDraft")}
              </Button>
            </section>
          )}
        </div>
      )}
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
        disabled={busy || review.phase !== "idle" || (!source && (!name.trim() || !path.trim()))}
        className="h-11 w-full"
      >
        {t(busy ? "setup.scanning" : source ? "setup.scanAgain" : "setup.connectScan")}
      </Button>
      <p className="text-xs leading-relaxed text-muted-foreground">{t("setup.folderDefaults")}</p>
    </form>
  );
}
