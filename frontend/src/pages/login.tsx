"use client";

import { userMessage } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { loginProvidersOptions } from "@/features/auth/entry";
import { Navigate } from "react-router-dom";
import { useRouter } from "@/lib/navigation";
import { AlertCircle, Clock3, ShieldCheck } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { ThemeToggle } from "@/components/theme-toggle";
import { LocaleToggle } from "@/components/locale-toggle";
import { consumeSessionExpired } from "@/lib/auth";
import { oidcLoginUrl } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-4 py-12">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/5 blur-3xl"
      />

      <div className="absolute right-5 top-5 flex items-center gap-1">
        <LocaleToggle />
        <ThemeToggle />
      </div>

      <div className="relative mx-auto w-full max-w-md">
        <Card className="border-outline-variant bg-surface-container-low shadow-lg">
          <CardHeader className="items-center px-6 pb-5 pt-8 text-center sm:px-8">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
              <BrandMark className="h-9 w-9" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-on-surface">
              {t("auth.welcome")}
            </h1>
            <CardDescription className="text-on-surface-variant">
              {t("auth.description")}
            </CardDescription>
          </CardHeader>

          <CardContent className="px-6 pb-8 sm:px-8">
            <form onSubmit={handleSubmit} className="space-y-5">
              {sessionExpired && (
                <div
                  role="status"
                  className="flex gap-2.5 rounded-md border border-warning/30 bg-warning/10 px-3 py-2.5 text-sm text-on-surface"
                >
                  <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                  <p>{t("auth.expired")}</p>
                </div>
              )}

              <div className="space-y-2">
                <label
                  htmlFor="username"
                  className="block text-xs font-mono uppercase tracking-wider text-on-surface-variant"
                >
                  {t("auth.username")}
                </label>
                <Input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  aria-invalid={!!error}
                  aria-describedby={error ? "login-error" : undefined}
                  autoComplete="username"
                  autoFocus
                  required
                  className="bg-surface-container-lowest font-mono text-on-surface"
                />
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="password"
                  className="block text-xs font-mono uppercase tracking-wider text-on-surface-variant"
                >
                  {t("auth.password")}
                </label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={!!error}
                  aria-describedby={error ? "login-error" : undefined}
                  autoComplete="current-password"
                  required
                  className="bg-surface-container-lowest font-mono text-on-surface"
                />
              </div>

              <div className="flex items-center gap-2.5">
                <Checkbox
                  checked={remember_me}
                  onChange={setremember_me}
                  ariaLabel={uiText("Remember me")}
                />
                <span className="text-sm text-on-surface-variant">{t("auth.remember")}</span>
              </div>

              {error && (
                <div
                  id="login-error"
                  role="alert"
                  className="flex gap-2.5 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive"
                >
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                  <p>{error}</p>
                </div>
              )}

              <Button type="submit" loading={busy} className="w-full">
                {t("auth.signIn")}
              </Button>
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
              <div className="mt-5 space-y-4">
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="h-px flex-1 bg-border" />
                  <span>{t("auth.or")}</span>
                  <span className="h-px flex-1 bg-border" />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  disabled={busy}
                  onClick={() => window.location.assign(oidcLoginUrl())}
                >
                  {t("auth.signInWith", { provider: providers.oidc_display_name })}
                </Button>
              </div>
            )}

            <div className="mt-6 flex items-center justify-center gap-2 border-t border-outline-variant pt-5 text-xs text-on-surface-variant">
              <ShieldCheck className="h-4 w-4 text-primary" aria-hidden />
              <span>{t("auth.local")}</span>
            </div>
          </CardContent>
        </Card>

        <p className="mt-5 text-center font-mono text-2xs uppercase tracking-wider text-muted-foreground">
          PrintStash · {t("auth.tagline")}
        </p>
      </div>
    </main>
  );
}
