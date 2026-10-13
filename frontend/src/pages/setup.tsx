import { storageOperationMessage } from "@/lib/storage-operations";
import { useUiLocale } from "@/lib/i18n";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  setupEntryOptions,
  checkSetupEntry,
  completeSetupEntry,
  type SetupEntryApi,
} from "@/features/setup/entry";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { Eye, EyeOff } from "lucide-react";
import { useRouter } from "@/lib/navigation";
import {
  beginSetup,
  checkSetupStorage,
  completeSetup,
  getSetupStatus,
  getStorageProviders,
} from "@/lib/api";
import {
  defaultProviderValues,
  StorageProviderPicker,
  type ProviderValues,
} from "@/components/storage-provider-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { storeLogin } from "@/lib/auth";
import { resetTasksForNewSetup } from "@/lib/task-center";
import { useI18n } from "@/lib/i18n";
import { SetupFrame } from "@/components/setup-frame";
import { SetupUnavailable } from "@/components/setup-unavailable";
import { setupErrorMessage, setupStorageBody } from "@/lib/setup-storage";
import { providerFormError } from "@/lib/storage-provider-form";
import { formatBytes } from "@/lib/format";
import type { SetupStorageCheck } from "@/types";

export interface SetupPageDeps extends SetupEntryApi {
  storeLogin: typeof storeLogin;
}
const LIVE_DEPS: SetupPageDeps = {
  getSetupStatus,
  getStorageProviders,
  beginSetup,
  checkSetupStorage,
  completeSetup,
  storeLogin,
};
type AccountField = "username" | "password" | "confirm" | "email";

export default function SetupPage({ deps = LIVE_DEPS }: { deps?: SetupPageDeps }) {
  const [port, setPort] = useState({ deps, generation: 0 });
  if (port.deps !== deps) setPort({ deps, generation: port.generation + 1 });
  return <SetupEntry key={port.generation} deps={deps} />;
}

function SetupEntry({ deps }: { deps: SetupPageDeps }) {
  useUiLocale();
  const router = useRouter();
  const { t } = useI18n();
  const entry = useId();
  const client = useQueryClient();
  const [bootAttempt, setBootAttempt] = useState(0);
  const bootstrapOptions = setupEntryOptions(deps, entry, bootAttempt);
  const bootstrap = useQuery(bootstrapOptions);
  const status = bootstrap.data?.status ?? null;
  const existing = bootstrap.data?.kind === "configured";
  const providers = bootstrap.data?.kind === "ready" ? bootstrap.data.providers : [];
  const [step, setStep] = useState<1 | 2>(1);
  const [account, setAccount] = useState({ username: "", password: "", confirm: "", email: "" });
  const [visiblePasswords, setVisiblePasswords] = useState({ password: false, confirm: false });
  const initialStorage = useMemo(() => {
    const state = bootstrap.data;
    if (!state || state.kind !== "ready") return { providerId: "local", values: {} };
    const id = state.status.current_storage_provider ?? "local";
    const provider = state.providers.find((item) => item.id === id);
    const values = provider ? defaultProviderValues(provider) : {};
    if (id === "local") {
      values.data_dir = state.status.current_data_dir ?? state.status.default_data_dir ?? "";
      values.thumb_dir = state.status.current_thumb_dir ?? state.status.default_thumb_dir ?? "";
    }
    Object.assign(values, state.status.current_storage_provider_config);
    return { providerId: id, values };
  }, [bootstrap.data]);
  const [storageDraft, setStorageDraft] = useState<{
    providerId: string;
    values: ProviderValues;
  } | null>(null);
  const { providerId, values } = storageDraft ?? initialStorage;
  const [check, setCheck] = useState<SetupStorageCheck | null>(null);
  const [checkedDraft, setCheckedDraft] = useState("");
  const [operation, setOperation] = useState<"check" | "create" | null>(null);
  const busy = operation !== null;
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<AccountField | null>(null);
  const view = useRef<AbortController | null>(null);
  const command = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  function describeError(error: Error): string {
    return setupErrorMessage(error, t);
  }

  useEffect(() => {
    const controller = new AbortController();
    view.current = controller;
    return () => controller.abort(new DOMException("setup_entry_disposed", "AbortError"));
  }, []);

  useEffect(() => {
    if (fieldError) form.current?.querySelector<HTMLInputElement>(`#setup-${fieldError}`)?.focus();
  }, [fieldError]);

  function next() {
    const invalid: AccountField | null =
      account.username.trim().length < 3
        ? "username"
        : account.password.length < 8
          ? "password"
          : account.password !== account.confirm
            ? "confirm"
            : account.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(account.email)
              ? "email"
              : null;
    setFieldError(invalid);
    if (invalid) form.current?.querySelector<HTMLInputElement>(`#setup-${invalid}`)?.focus();
    if (!invalid) {
      setStep(2);
      setError("");
    }
  }
  function storageBody() {
    return setupStorageBody(providerId, values);
  }
  async function checkStorage() {
    const controller = view.current;
    if (!controller || controller.signal.aborted || command.current) return;
    const version = getSessionVersion();
    const provider = providers.find((item) => item.id === providerId);
    const invalid = !provider?.selectable
      ? provider?.disabled_reason
        ? storageOperationMessage(provider.disabled_reason, t)
        : t("setup.providerUnavailable")
      : providerFormError(provider, values);
    if (invalid) {
      setCheck(null);
      setError(invalid);
      return;
    }
    command.current = true;
    setOperation("check");
    setError("");
    setCheck(null);
    try {
      const draft = storageBody();
      setCheckedDraft(JSON.stringify(draft));
      const checked = await checkSetupEntry(deps, draft, controller.signal);
      requireSessionVersion(version);
      if (!controller.signal.aborted) setCheck(checked);
    } catch (error) {
      if (!controller.signal.aborted && version === getSessionVersion())
        setError(describeError(error instanceof Error ? error : new Error()));
    } finally {
      command.current = false;
      if (!controller.signal.aborted) setOperation(null);
    }
  }
  async function submit() {
    const controller = view.current;
    if (
      !controller ||
      controller.signal.aborted ||
      command.current ||
      busy ||
      !check?.ready ||
      checkedDraft !== JSON.stringify(storageBody())
    )
      return;
    const version = getSessionVersion();
    const body = {
      ...storageBody(),
      username: account.username.trim(),
      password: account.password,
      email: account.email.trim() || undefined,
    };
    command.current = true;
    setOperation("create");
    setError("");
    try {
      const completion = await completeSetupEntry(deps, body, controller.signal);
      requireSessionVersion(version);
      if (controller.signal.aborted) return;
      resetTasksForNewSetup();
      if (completion.kind === "configured") {
        client.setQueryData(bootstrapOptions.queryKey, {
          kind: "configured",
          status: completion.status,
        });
        return;
      }
      const result = completion.response;
      // This verified publication intentionally starts the authenticated scope.
      deps.storeLogin(result.access_token, {
        id: result.user_id,
        username: result.username,
        email: body.email ?? null,
        is_superuser: true,
      });
      setAccount({ username: "", password: "", confirm: "", email: "" });
      router.replace("/getting-started");
    } catch (error) {
      if (!controller.signal.aborted && version === getSessionVersion())
        setError(describeError(error instanceof Error ? error : new Error()));
    } finally {
      command.current = false;
      if (!controller.signal.aborted) setOperation(null);
    }
  }
  const currentCheck = checkedDraft === JSON.stringify(storageBody()) ? check : null;
  const errors = {
    username: t("setup.badUsername"),
    password: t("setup.badPassword"),
    confirm: t("setup.mismatch"),
    email: t("setup.badEmail"),
  };
  return (
    <SetupFrame step={step}>
      {existing ? (
        <div className="space-y-5">
          <p role="status">{t("setup.exists")}</p>
          <Button onClick={() => router.replace("/login")}>{t("setup.login")}</Button>
        </div>
      ) : !status ? (
        <div role="status">
          {bootstrap.error ? t("setup.failed") : t("setup.loading")}
          {bootstrap.error && (
            <Button onClick={() => setBootAttempt((n) => n + 1)}>{t("setup.retry")}</Button>
          )}
        </div>
      ) : !status.setup_available ? (
        <SetupUnavailable
          reason={status.unavailable_reason ?? "disabled"}
          host={status.observed_host ?? window.location.hostname}
          variables={status.unavailable_variables ?? []}
          onRetry={() => setBootAttempt((n) => n + 1)}
        />
      ) : (
        <form
          ref={form}
          noValidate
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (step === 1) next();
            else void submit();
          }}
        >
          <div>
            <h2 ref={heading} tabIndex={-1} className="text-2xl font-bold tracking-tight">
              {t(step === 1 ? "setup.account" : "setup.files")}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              {t(step === 1 ? "setup.accountHelp" : "setup.defaultHelp")}
            </p>
          </div>
          {step === 1 ? (
            <div className="grid gap-5 sm:grid-cols-2">
              {(["username", "password", "confirm", "email"] as const).map((field) => (
                <div
                  key={field}
                  className={
                    field === "username" || field === "email"
                      ? "space-y-2 sm:col-span-2"
                      : "space-y-2"
                  }
                >
                  <label
                    htmlFor={`setup-${field}`}
                    className="block text-xs font-medium text-on-surface-variant"
                  >
                    {t(`setup.${field}`)}
                  </label>
                  <div className="relative">
                    <Input
                      id={`setup-${field}`}
                      value={account[field]}
                      autoFocus={field === "username"}
                      required={field !== "email"}
                      maxLength={field === "username" ? 128 : 256}
                      type={
                        field === "password" || field === "confirm"
                          ? visiblePasswords[field]
                            ? "text"
                            : "password"
                          : field === "email"
                            ? "email"
                            : "text"
                      }
                      autoComplete={
                        field === "password" || field === "confirm" ? "new-password" : field
                      }
                      aria-invalid={fieldError === field}
                      aria-describedby={`setup-${field}-help`}
                      className={
                        field === "password" || field === "confirm"
                          ? "h-11 bg-surface-container-lowest pr-12"
                          : "h-11 bg-surface-container-lowest"
                      }
                      onChange={(event) => {
                        setAccount((current) => ({ ...current, [field]: event.target.value }));
                        if (fieldError === field) setFieldError(null);
                      }}
                    />
                    {(field === "password" || field === "confirm") && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="absolute inset-y-0 right-0 h-full w-11 text-muted-foreground"
                        aria-label={t(
                          visiblePasswords[field] ? "setup.hideField" : "setup.showField",
                          { field: t(`setup.${field}`) },
                        )}
                        aria-controls={`setup-${field}`}
                        aria-pressed={visiblePasswords[field]}
                        onClick={() =>
                          setVisiblePasswords((current) => ({
                            ...current,
                            [field]: !current[field],
                          }))
                        }
                      >
                        {visiblePasswords[field] ? (
                          <EyeOff className="h-4 w-4" aria-hidden />
                        ) : (
                          <Eye className="h-4 w-4" aria-hidden />
                        )}
                      </Button>
                    )}
                  </div>
                  <p
                    id={`setup-${field}-help`}
                    role={fieldError === field ? "alert" : undefined}
                    className={
                      fieldError === field
                        ? "text-sm text-destructive"
                        : "text-xs text-muted-foreground"
                    }
                  >
                    {fieldError === field
                      ? errors[field]
                      : field === "password"
                        ? t("setup.passwordHelp")
                        : field === "email"
                          ? t("setup.emailHelp")
                          : ""}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <>
              <div className="space-y-4">
                <StorageProviderPicker
                  onboarding
                  disabled={operation === "create"}
                  providers={providers}
                  providerId={providerId}
                  values={values}
                  onProviderChange={(provider) => {
                    const defaults = defaultProviderValues(provider);
                    if (provider.id === "local") {
                      defaults.data_dir = status.current_data_dir ?? status.default_data_dir ?? "";
                      defaults.thumb_dir =
                        status.current_thumb_dir ?? status.default_thumb_dir ?? "";
                    }
                    setStorageDraft({ providerId: provider.id, values: defaults });
                    setCheck(null);
                    setError("");
                  }}
                  onValueChange={(name, value) => {
                    setStorageDraft((current) => ({
                      providerId,
                      values: { ...(current?.values ?? values), [name]: value },
                    }));
                    setCheck(null);
                    setError("");
                  }}
                />
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {t("setup.existingHelp")}
                </p>
              </div>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              <div className="space-y-3 border-t border-border pt-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-5">
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t("setup.checkFirst")}
                  </p>
                  <Button
                    type="button"
                    variant={currentCheck?.ready ? "outline" : "default"}
                    loading={operation === "check"}
                    disabled={operation === "create"}
                    className="shrink-0"
                    onClick={() => void checkStorage()}
                  >
                    {t("setup.check")}
                  </Button>
                </div>
                <div role="status" aria-live="polite">
                  {busy ? (
                    t(operation === "check" ? "setup.checking" : "setup.creating")
                  ) : currentCheck ? (
                    <>
                      <p>{t(currentCheck.ready ? "setup.ready" : "setup.attention")}</p>
                      {!currentCheck.ready && <p>{t("setup.remoteCheck")}</p>}
                      {currentCheck.checks.map((item) =>
                        item.free_bytes === null ? null : (
                          <p className="text-xs text-muted-foreground" key={item.code}>
                            {item.code === "data_writable" ? t("setup.files") : t("setup.previews")}
                            : {formatBytes(item.free_bytes)} {t("setup.availableSpace")}
                          </p>
                        ),
                      )}
                    </>
                  ) : null}
                </div>
                <p className="text-xs text-muted-foreground">{t("setup.checkHelp")}</p>
              </div>
              <section aria-label={t("setup.summary")}>
                <p className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 break-all">
                    <span className="text-muted-foreground">{t("setup.username")}: </span>
                    {account.username.trim()}{" "}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => setStep(1)}
                  >
                    {t("setup.edit")}
                  </Button>
                </p>
              </section>
            </>
          )}
          {error && step === 1 && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex gap-3 border-t border-border pt-5">
            {step === 2 ? (
              <Button
                type="button"
                variant="outline"
                className="h-11"
                disabled={busy}
                onClick={() => setStep(1)}
              >
                {t("setup.back")}
              </Button>
            ) : null}
            <Button
              type="submit"
              className="h-auto min-h-11 flex-1 whitespace-normal py-2.5"
              loading={busy}
              disabled={step === 2 && !currentCheck?.ready}
            >
              {t(step === 1 ? "setup.next" : "setup.create")}
            </Button>
          </div>
        </form>
      )}
    </SetupFrame>
  );
}
