import { storageOperationMessage } from "@/lib/storage-operations";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useEffect, useRef, useState } from "react";
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
import { useI18n } from "@/lib/i18n";
import { SetupFrame } from "@/components/setup-frame";
import { providerFormError } from "@/lib/storage-provider-form";
import { formatBytes } from "@/lib/format";
import type { SetupStatus, SetupStorageCheck, StorageProvider } from "@/types";

export interface SetupPageDeps {
  getSetupStatus: typeof getSetupStatus;
  getStorageProviders: typeof getStorageProviders;
  beginSetup: typeof beginSetup;
  checkSetupStorage: typeof checkSetupStorage;
  completeSetup: typeof completeSetup;
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
  useUiLocale();
  const router = useRouter();
  const { t } = useI18n();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [step, setStep] = useState<1 | 2>(1);
  const [account, setAccount] = useState({ username: "", password: "", confirm: "", email: "" });
  const [visiblePasswords, setVisiblePasswords] = useState({ password: false, confirm: false });
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [providerId, setProviderId] = useState("local");
  const [values, setValues] = useState<ProviderValues>({});
  const [check, setCheck] = useState<SetupStorageCheck | null>(null);
  const [checkedDraft, setCheckedDraft] = useState("");
  const [operation, setOperation] = useState<"check" | "create" | null>(null);
  const busy = operation !== null;
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<AccountField | null>(null);
  const [existing, setExisting] = useState(false);
  const [bootAttempt, setBootAttempt] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  function describeError(error: Error): string {
    const raw = error.message;
    if (/already_configured|users_already_exist/.test(raw)) return t("setup.exists");
    if (/not_empty/.test(raw)) return t("setup.populated");
    if (/setup_session/.test(raw)) return t("setup.session");
    if (/setup_origin/.test(raw)) return t("setup.origin");
    if (/setup_disabled/.test(raw)) return t("setup.disabled");
    if (/setup_remote_storage/.test(raw)) return t("setup.remoteFailure");
    if (/dir|path|writable|readable/.test(raw)) return t("setup.paths");
    return t("setup.failed");
  }

  useEffect(() => {
    let cancelled = false;
    async function prepare() {
      const state = await deps.getSetupStatus();
      if (cancelled) return;
      if (state.configured) {
        setExisting(true);
        setStatus(state);
        return;
      }
      setStatus(state);
      if (!state.setup_available) return;
      await deps.beginSetup();
      const catalog = await deps.getStorageProviders();
      if (cancelled) return;
      setProviders(catalog);
      const id = state.current_storage_provider ?? "local";
      setProviderId(id);
      const provider = catalog.find((item) => item.id === id);
      const initial = provider ? defaultProviderValues(provider) : {};
      if (id === "local") {
        initial.data_dir = state.current_data_dir ?? state.default_data_dir ?? "";
        initial.thumb_dir = state.current_thumb_dir ?? state.default_thumb_dir ?? "";
      }
      Object.assign(initial, state.current_storage_provider_config);
      setValues(initial);
    }
    void prepare().catch(() => {
      if (!cancelled) setError(uiText("setup.failed"));
    });
    return () => {
      cancelled = true;
    };
  }, [deps, bootAttempt]);

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
    return {
      storage_provider: providerId,
      storage_provider_config: {
        provider: providerId,
        ...Object.fromEntries(
          Object.entries(values).filter(
            ([key, value]) => key !== "secret_fields_set" && value !== "",
          ),
        ),
      },
    };
  }
  async function checkStorage() {
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
    setOperation("check");
    setError("");
    setCheck(null);
    try {
      const draft = storageBody();
      setCheckedDraft(JSON.stringify(draft));
      const session = await deps.beginSetup();
      setCheck(await deps.checkSetupStorage(draft, session.csrf));
    } catch (error) {
      setError(describeError(error instanceof Error ? error : new Error()));
    } finally {
      setOperation(null);
    }
  }
  async function submit() {
    if (busy || !check?.ready || checkedDraft !== JSON.stringify(storageBody())) return;
    setOperation("create");
    setError("");
    try {
      const session = await deps.beginSetup();
      const result = await deps.completeSetup(
        {
          ...storageBody(),
          username: account.username.trim(),
          password: account.password,
          email: account.email.trim() || undefined,
        },
        session.csrf,
      );
      deps.storeLogin(result.access_token, {
        id: result.user_id,
        username: result.username,
        email: account.email.trim() || null,
        is_superuser: true,
      });
      setAccount({ username: "", password: "", confirm: "", email: "" });
      router.replace("/getting-started");
    } catch (error) {
      // A lost HTTP response does not imply that the database rolled back.
      try {
        const state = await deps.getSetupStatus();
        if (state.configured) {
          setExisting(true);
          return;
        }
      } catch {
        /* Keep the form for recovery. */
      }
      setError(describeError(error instanceof Error ? error : new Error()));
    } finally {
      setOperation(null);
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
          {error ? t("setup.failed") : t("setup.loading")}
          {error && (
            <Button onClick={() => setBootAttempt((n) => n + 1)}>{t("setup.retry")}</Button>
          )}
        </div>
      ) : !status.setup_available ? (
        <p role="alert">{t("setup.disabled")}</p>
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
                    setProviderId(provider.id);
                    const defaults = defaultProviderValues(provider);
                    if (provider.id === "local") {
                      defaults.data_dir = status.current_data_dir ?? status.default_data_dir ?? "";
                      defaults.thumb_dir =
                        status.current_thumb_dir ?? status.default_thumb_dir ?? "";
                    }
                    setValues(defaults);
                    setCheck(null);
                    setError("");
                  }}
                  onValueChange={(name, value) => {
                    setValues((current) => ({ ...current, [name]: value }));
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
