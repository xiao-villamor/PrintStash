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
import { AlertTriangle, CheckCircle2, Eye, EyeOff } from "lucide-react";
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
import {
  authFieldError,
  authFieldHelp,
  authFieldInput,
  authFieldLabel,
} from "@/components/auth-field-classes";
import { storeLogin } from "@/lib/auth";
import { resetTasksForNewSetup } from "@/lib/task-center";
import { useI18n } from "@/lib/i18n";
import { SetupFrame } from "@/components/setup-frame";
import { SetupUnavailable } from "@/components/setup-unavailable";
import { setupErrorMessage, setupStorageBody } from "@/lib/setup-storage";
import { providerFormError } from "@/lib/storage-provider-form";
import { formatBytes } from "@/lib/format";
import { knownUiText } from "@/lib/locale";
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
const ACCOUNT_FIELDS = ["username", "email", "password", "confirm"] as const;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Every account rule that fails, in form order, so all of them are reported at once. */
function invalidAccountFields(account: Record<AccountField, string>): AccountField[] {
  return ACCOUNT_FIELDS.filter((field) => {
    if (field === "username") return account.username.trim().length < 3;
    if (field === "password") return account.password.length < 8;
    if (field === "confirm") return account.password !== account.confirm;
    return account.email !== "" && !EMAIL_PATTERN.test(account.email);
  });
}

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
  const [step, setStep] = useState<1 | 2 | 3>(1);
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
  const [fieldErrors, setFieldErrors] = useState<readonly AccountField[]>([]);
  // Continue on the storage step runs the check and advances once it reports ready.
  const [advanceWhenChecked, setAdvanceWhenChecked] = useState(false);
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

  function next() {
    const invalid = invalidAccountFields(account);
    setFieldErrors(invalid);
    if (invalid.length > 0) {
      form.current?.querySelector<HTMLInputElement>(`#setup-${invalid[0]}`)?.focus();
      return;
    }
    setStep(2);
    setError("");
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
  // The check started by Continue has settled: advance only on a ready result for
  // the values still on screen; anything else leaves its message in place.
  if (advanceWhenChecked && operation === null) {
    setAdvanceWhenChecked(false);
    if (currentCheck?.ready) setStep(3);
  }
  const selectedProvider = providers.find((provider) => provider.id === providerId);
  const filesFree = currentCheck?.checks.find((item) => item.code === "data_writable")?.free_bytes;
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
            else if (step === 2) {
              if (currentCheck?.ready) setStep(3);
              else {
                setAdvanceWhenChecked(true);
                void checkStorage();
              }
            } else if (step === 3) void submit();
          }}
        >
          <div>
            <h2
              ref={heading}
              tabIndex={-1}
              className="text-base font-semibold tracking-tight text-foreground focus-visible:outline-none"
            >
              {t(
                step === 1 ? "setup.accountHeading" : step === 2 ? "setup.files" : "setup.summary",
              )}
            </h2>
            {step !== 3 && (
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {step === 1
                  ? t("setup.accountHelp")
                  : `${t("setup.defaultHelp")} ${t("setup.existingHelp")}`}
              </p>
            )}
          </div>
          {step === 1 ? (
            <div className="grid gap-4">
              {ACCOUNT_FIELDS.map((field) => {
                const invalid = fieldErrors.includes(field);
                const secret = field === "password" || field === "confirm";
                const help =
                  field === "password"
                    ? t("setup.passwordHelp")
                    : field === "email"
                      ? t("setup.emailHelp")
                      : null;
                const described = invalid || help !== null;
                return (
                  <div key={field} className="grid gap-1.5">
                    <label htmlFor={`setup-${field}`} className={authFieldLabel}>
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
                          secret
                            ? visiblePasswords[field]
                              ? "text"
                              : "password"
                            : field === "email"
                              ? "email"
                              : "text"
                        }
                        autoComplete={secret ? "new-password" : field}
                        aria-invalid={invalid}
                        aria-describedby={described ? `setup-${field}-help` : undefined}
                        className={secret ? `${authFieldInput} pr-10` : authFieldInput}
                        onChange={(event) => {
                          setAccount((current) => ({ ...current, [field]: event.target.value }));
                          if (invalid)
                            setFieldErrors((current) => current.filter((item) => item !== field));
                        }}
                      />
                      {secret && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute inset-y-0 right-0 h-full w-10 text-muted-foreground"
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
                    {described && (
                      <p
                        id={`setup-${field}-help`}
                        role={invalid ? "alert" : undefined}
                        className={invalid ? authFieldError : authFieldHelp}
                      >
                        {invalid ? errors[field] : help}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          ) : step === 2 ? (
            <>
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
                    defaults.thumb_dir = status.current_thumb_dir ?? status.default_thumb_dir ?? "";
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
              <div className="space-y-3 border-t border-border pt-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-5">
                  <div>
                    <p className="text-[13px] font-medium text-foreground">
                      {t("setup.checkFirst")}
                    </p>
                    <p className={`mt-1 ${authFieldHelp}`}>{t("setup.checkHelp")}</p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    loading={operation === "check"}
                    disabled={operation === "create"}
                    className="shrink-0"
                    onClick={() => void checkStorage()}
                  >
                    {t("setup.check")}
                  </Button>
                </div>
                {error && (
                  <p role="alert" className="text-sm text-destructive">
                    {error}
                  </p>
                )}
                <div role="status" aria-live="polite" className="text-[13px]">
                  {busy ? (
                    <p className="text-muted-foreground">
                      {t(operation === "check" ? "setup.checking" : "setup.creating")}
                    </p>
                  ) : currentCheck ? (
                    <div className="space-y-1.5">
                      <p
                        className={
                          currentCheck.ready
                            ? "flex items-center gap-1.5 font-medium text-success"
                            : "flex items-center gap-1.5 font-medium text-warning"
                        }
                      >
                        {currentCheck.ready ? (
                          <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
                        ) : (
                          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                        )}
                        {t(currentCheck.ready ? "setup.ready" : "setup.attention")}
                      </p>
                      {!currentCheck.ready && (
                        <p className="text-muted-foreground">{t("setup.remoteCheck")}</p>
                      )}
                      {currentCheck.checks.some((item) => item.free_bytes !== null) && (
                        <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-1">
                          {currentCheck.checks.map((item) =>
                            item.free_bytes === null ? null : (
                              <div key={item.code} className="contents">
                                <dt className="text-muted-foreground">
                                  {item.code === "data_writable"
                                    ? t("setup.files")
                                    : t("setup.previews")}
                                </dt>
                                <dd className="font-mono text-xs leading-5 text-foreground">
                                  {formatBytes(item.free_bytes)} {t("setup.availableSpace")}
                                </dd>
                              </div>
                            ),
                          )}
                        </dl>
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            </>
          ) : (
            <dl
              aria-label={t("setup.summary")}
              className="divide-y divide-border border-y border-border text-sm"
            >
              {(
                [
                  {
                    key: "username",
                    label: t("setup.username"),
                    value: account.username.trim(),
                    edit: { step: 1, label: t("setup.editAccount") },
                  },
                  {
                    key: "email",
                    label: t("setup.reviewEmail"),
                    value: account.email.trim() || "—",
                  },
                  {
                    key: "files",
                    label: t("setup.files"),
                    value: [
                      knownUiText(selectedProvider?.label ?? providerId),
                      values.data_dir ? String(values.data_dir) : null,
                      filesFree != null
                        ? `${formatBytes(filesFree)} ${t("setup.availableSpace")}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · "),
                    edit: { step: 2, label: t("setup.editStorage") },
                  },
                  ...(values.thumb_dir
                    ? [
                        {
                          key: "previews",
                          label: t("setup.previews"),
                          value: String(values.thumb_dir),
                        },
                      ]
                    : []),
                ] as const satisfies readonly {
                  key: string;
                  label: string;
                  value: string;
                  edit?: { step: 1 | 2; label: string };
                }[]
              ).map((row) => (
                <div
                  key={row.key}
                  className="grid min-h-12 grid-cols-[8rem_minmax(0,1fr)_auto] items-center gap-3 py-2"
                >
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className="min-w-0 break-words">{row.value}</dd>
                  <dd>
                    {"edit" in row && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => setStep(row.edit.step)}
                      >
                        {row.edit.label}
                      </Button>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {error && step !== 2 && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {step === 3 && busy && (
            <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
              {t("setup.creating")}
            </p>
          )}
          <div className="flex justify-between gap-3 border-t border-border pt-5">
            {step !== 1 ? (
              <Button
                type="button"
                variant="outline"
                className="h-10"
                disabled={busy}
                onClick={() => setStep(step === 3 ? 2 : 1)}
              >
                {t("setup.back")}
              </Button>
            ) : null}
            <Button
              type="submit"
              className="ml-auto h-auto min-h-10 whitespace-normal py-2"
              loading={busy}
            >
              {t(step === 3 ? "setup.create" : "setup.next")}
            </Button>
          </div>
        </form>
      )}
    </SetupFrame>
  );
}
