"use client";

import { userMessage } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { loginProvidersOptions } from "@/features/auth/entry";
import { Navigate } from "@tanstack/react-router";
import { useRouter } from "@/lib/navigation";
import { AlertCircle, Info, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { AuthFrame } from "@/components/auth-frame";
import { authFieldInput, authFieldLabel } from "@/components/auth-field-classes";
import { consumeSessionExpired } from "@/lib/auth";
import { oidcLoginUrl } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Where an operator learns how a forgotten password is reset; there is no self-service reset. */
const PASSWORD_RECOVERY_DOCS =
  "https://github.com/xiao-villamor/PrintStash/blob/main/docs/first-run.md#forgotten-password";

/** What the OIDC redirect back to /login is telling us, read once from the URL. */
type OidcCallback = "none" | "success" | "failed";

function readOidcCallback(): OidcCallback {
  const params = new URLSearchParams(window.location.search);
  if (params.get("oidc") === "success") return "success";
  if (params.has("oidc_error")) return "failed";
  return "none";
}

export default function LoginPage() {
  useUiLocale();
  const { login, refresh, user } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember_me, setremember_me] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const providerRead = useQuery(loginProvidersOptions());
  const providers = providerRead.data;
  const view = useRef<AbortController | null>(null);
  const submitting = useRef(false);
  const [sessionExpired] = useState(consumeSessionExpired);
  // The redirect outcome is fixed by the URL we mounted on, so it is read once
  // rather than re-derived (router.replace below rewrites the query string).
  const [oidcCallback] = useState(readOidcCallback);
  const [ssoFailed, setSsoFailed] = useState(oidcCallback === "failed");
  // A "?oidc=success" landing is already mid-exchange: the session refresh below
  // starts on mount, so the form is busy from the very first paint.
  const [busy, setBusy] = useState(oidcCallback === "success");

  // Kept out of state so the message follows the locale toggle on this page.
  const error = formError ?? (ssoFailed ? t("auth.ssoFailed") : null);

  useEffect(() => {
    const controller = new AbortController();
    view.current = controller;
    return () => controller.abort(new DOMException("login_entry_disposed", "AbortError"));
  }, []);

  useEffect(() => {
    if (oidcCallback !== "success") return;
    const controller = new AbortController();
    void refresh(controller.signal)
      .then(() => {
        if (!controller.signal.aborted) router.replace("/");
      })
      .catch(() => {
        if (!controller.signal.aborted) setSsoFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort(new DOMException("oidc_entry_disposed", "AbortError"));
  }, [oidcCallback, refresh, router]);

  if (user) {
    return <Navigate to="/" replace />;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const controller = view.current;
    if (!controller || controller.signal.aborted || submitting.current || busy) return;
    submitting.current = true;
    setFormError(null);
    setSsoFailed(false);
    setBusy(true);
    try {
      await login(username, password, remember_me, controller.signal);
      if (!controller.signal.aborted) router.replace("/");
    } catch (err) {
      if (controller.signal.aborted) return;
      if (err instanceof Error && err.message.includes("401")) {
        setFormError(t("auth.invalid"));
      } else {
        setFormError(userMessage(err));
      }
    } finally {
      submitting.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <AuthFrame>
      <h1 className="text-[22px] font-semibold tracking-tight text-foreground">
        {t("auth.welcome")}
      </h1>
      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t("auth.description")}</p>

      <form onSubmit={handleSubmit} className="mt-5 grid gap-4">
        {sessionExpired && (
          <div
            role="status"
            className="flex items-start gap-2.5 border border-border bg-muted/50 px-3 py-2.5 text-[13px] font-medium text-foreground"
          >
            <Info className="mt-px h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <p>{t("auth.expired")}</p>
          </div>
        )}

        {error && (
          <div
            id="login-error"
            role="alert"
            className="flex items-start gap-2.5 border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-[13px] font-medium text-foreground"
          >
            <AlertCircle className="mt-px h-4 w-4 shrink-0 text-destructive" aria-hidden />
            <p>{error}</p>
          </div>
        )}

        <div className="grid gap-1.5">
          <label htmlFor="username" className={authFieldLabel}>
            {t("auth.username")}
          </label>
          <Input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            aria-describedby={error ? "login-error" : undefined}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            required
            className={authFieldInput}
          />
        </div>

        <div className="grid gap-1.5">
          <label htmlFor="password" className={authFieldLabel}>
            {t("auth.password")}
          </label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby={error ? "login-error" : undefined}
            autoComplete="current-password"
            required
            className={authFieldInput}
          />
        </div>

        {/* The whole row toggles the box, as a native checkbox label would. */}
        <label className="flex min-h-6 w-fit cursor-action items-center gap-2.5 text-[13px] text-on-surface-variant">
          <Checkbox
            checked={remember_me}
            onChange={setremember_me}
            ariaLabel={uiText("Remember me")}
          />
          <span>{t("auth.remember")}</span>
        </label>

        <Button type="submit" loading={busy} className="mt-1 h-10 w-full">
          {t("auth.signIn")}
        </Button>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("auth.forgot")}{" "}
          <a
            href={PASSWORD_RECOVERY_DOCS}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-foreground underline underline-offset-2 hover:text-primary"
          >
            {t("auth.forgotLink")}
          </a>
        </p>
      </form>

      {providerRead.error && (
        <div className="mt-5 space-y-3">
          <p role="alert" className="text-sm text-destructive">
            {userMessage(providerRead.error)}
          </p>
          <Button type="button" variant="outline" onClick={() => void providerRead.refetch()}>
            {uiText("Retry")}
          </Button>
        </div>
      )}
      {providers?.oidc_enabled && (
        <>
          <div
            role="separator"
            className="my-5 flex items-center gap-3 text-xs text-muted-foreground"
          >
            <span className="h-px flex-1 bg-border" />
            <span>{t("auth.or")}</span>
            <span className="h-px flex-1 bg-border" />
          </div>
          <Button
            type="button"
            variant="outline"
            className="h-10 w-full"
            disabled={busy}
            onClick={() => window.location.assign(oidcLoginUrl())}
          >
            {t("auth.signInWith", { provider: providers.oidc_display_name })}
          </Button>
        </>
      )}

      <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
        {t("auth.local")}
      </p>
    </AuthFrame>
  );
}
