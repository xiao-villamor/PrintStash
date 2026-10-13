"use client";

import { uiText } from "@/lib/locale";
import { useI18n, useUiLocale } from "@/lib/i18n";

import { useEffect, useRef, useState } from "react";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { useVaultConfig } from "@/lib/queries";
import { useVaultConfigCommand } from "@/lib/queries/settings-config";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { captureEditingBase } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { parseApiError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import type { VaultConfigRead, VaultConfigUpdate } from "@/types";

type OidcDraft = Pick<
  VaultConfigRead,
  | "oidc_enabled"
  | "oidc_issuer_url"
  | "oidc_client_id"
  | "oidc_scopes"
  | "oidc_username_claim"
  | "oidc_groups_claim"
  | "oidc_admin_groups"
  | "oidc_display_name"
  | "oidc_redirect_uri"
  | "oidc_allow_insecure_http"
>;

/** The config slice this card reads back from a load or a save. */
export type OidcConfig = OidcDraft & Pick<VaultConfigRead, "has_oidc_client_secret">;

/** The patch this card sends: its own fields, plus the optional new secret. */
export type OidcConfigUpdate = OidcDraft & Pick<VaultConfigUpdate, "oidc_client_secret">;

function oidcDraft(config: VaultConfigRead): OidcDraft {
  return {
    oidc_enabled: config.oidc_enabled,
    oidc_issuer_url: config.oidc_issuer_url,
    oidc_client_id: config.oidc_client_id,
    oidc_scopes: config.oidc_scopes,
    oidc_username_claim: config.oidc_username_claim,
    oidc_groups_claim: config.oidc_groups_claim,
    oidc_admin_groups: config.oidc_admin_groups,
    oidc_display_name: config.oidc_display_name,
    oidc_redirect_uri: config.oidc_redirect_uri,
    oidc_allow_insecure_http: config.oidc_allow_insecure_http,
  };
}

function Field({
  label,
  hint,
  ...props
}: React.ComponentProps<typeof Input> & { label: string; hint?: string }) {
  useUiLocale();
  return (
    <label className="space-y-1.5">
      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <Input {...props} />
      {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

/** The canonical full config is the remote owner; this editor keeps only its draft. */
export function OidcSettingsCard() {
  useUiLocale();
  const { t } = useI18n();
  const { user } = useAuth();
  const query = useVaultConfig({ enabled: !!user?.is_superuser, retry: false });
  const denied = query.isError && [401, 403, 404].includes(parseApiError(query.error).status);
  return (
    <Localized>
      <>
        {query.isError && (
          <div role="alert" className="flex items-center gap-2 text-sm">
            <p>{t("settings.configLoadFailed")}</p>
            <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        {query.isPending && <p role="status">{uiText("Loading…")}</p>}
        {query.data && !denied && user?.is_superuser && (
          <OidcEditor
            config={query.data}
            unavailable={query.isError}
            reload={async () => {
              const result = await query.refetch({ throwOnError: true });
              if (!result.data) throw new Error("Configuration review has no data");
              return result.data;
            }}
          />
        )}
      </>
    </Localized>
  );
}
type ConfigReview =
  | { phase: "idle" }
  | { phase: "required" | "loading"; problem: "conflict" | "unconfirmed" }
  | { phase: "ready"; problem: "conflict" | "unconfirmed"; snapshot: VaultConfigRead };
function OidcEditor({
  config,
  unavailable,
  reload,
}: {
  config: VaultConfigRead;
  unavailable: boolean;
  reload: () => Promise<VaultConfigRead>;
}) {
  useUiLocale();
  const [chosenDraft, setDraft] = useState(() => oidcDraft(config));
  const [edited, setEdited] = useState(false);
  const [base, setBase] = useState(() => captureEditingBase(config));
  const [review, setReview] = useState<ConfigReview>({ phase: "idle" });
  const draft = edited ? chosenDraft : oidcDraft(config);
  const [clientSecret, setClientSecret] = useState("");
  const [clearClientSecret, setClearClientSecret] = useState(false);
  const command = useVaultConfigCommand();
  const saving = command.isPending;
  const loading = unavailable;
  const live = useRef(true);
  const generation = useRef(0);
  const [retired, setRetired] = useState(false);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      generation.current += 1;
      setClientSecret("");
      setClearClientSecret(false);
      setRetired(true);
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const hasClientSecret = config.has_oidc_client_secret;
  function set<K extends keyof OidcDraft>(key: K, value: OidcDraft[K]) {
    generation.current += 1;
    if (!edited) setBase(captureEditingBase(config));
    setDraft((current) => ({ ...(edited ? current : oidcDraft(config)), [key]: value }));
    setEdited(true);
  }

  async function save(reviewedBase?: EditingBase) {
    if (draft.oidc_enabled && (!draft.oidc_issuer_url.trim() || !draft.oidc_client_id.trim())) {
      toast.error(uiText("Issuer URL and client ID are required before enabling SSO."));
      return;
    }
    if (loading || retired || saving || (review.phase !== "idle" && !reviewedBase)) return;
    const session = getSessionVersion();
    const captured = generation.current;
    try {
      const payload: OidcConfigUpdate = { ...draft };
      if (clientSecret) payload.oidc_client_secret = clientSecret;
      else if (clearClientSecret) payload.oidc_client_secret = "";
      const acknowledged = await command.mutateAsync({
        session,
        payload,
        base: reviewedBase ?? (edited ? base : captureEditingBase(config)),
      });
      if (!live.current || session !== getSessionVersion()) return;
      if (captured === generation.current) {
        setDraft(oidcDraft(acknowledged));
        setEdited(false);
        setBase(captureEditingBase(acknowledged));
        setReview({ phase: "idle" });
        setClientSecret("");
        setClearClientSecret(false);
      }
      toast.success(uiText("Single sign-on settings saved."));
    } catch (error) {
      if (live.current && session === getSessionVersion()) {
        const status = parseApiError(error).status;
        if (status === 412 || status === 428 || status === 0 || status >= 500)
          setReview({
            phase: "required",
            problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
          });
        toast.error(error);
      }
    }
  }

  async function reviewLatest() {
    if (review.phase === "idle" || review.phase === "loading" || saving) return;
    const session = getSessionVersion();
    const problem = review.problem;
    setReview({ phase: "loading", problem });
    try {
      const snapshot = await reload();
      if (live.current && session === getSessionVersion())
        setReview({ phase: "ready", problem, snapshot });
    } catch (error) {
      if (live.current && session === getSessionVersion()) {
        setReview({ phase: "required", problem });
        toast.error(error);
      }
    }
  }
  if (retired) return null;
  return (
    <Localized>
      <Card className="animate-panel-in overflow-hidden">
        <CardHeader className="border-b border-border p-4 sm:p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-muted text-muted-foreground">
                <ShieldCheck className="h-4 w-4" />
              </div>
              <div>
                <CardTitle className="text-sm">{uiText("OpenID Connect")}</CardTitle>
                <CardDescription className="mt-1 text-xs">
                  {uiText(
                    "Connect Authentik, Authelia, Keycloak, or another standards-compatible identity provider. Local login stays available.",
                  )}
                </CardDescription>
              </div>
            </div>
            {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          </div>
        </CardHeader>
        <CardContent className="space-y-5 p-4 sm:p-5">
          {review.phase !== "idle" && (
            <div role="alert" className="space-y-3">
              <p>
                {uiText(
                  review.problem === "conflict"
                    ? "library.editConflict"
                    : "library.saveUnconfirmed",
                )}
              </p>
              <Button
                variant="outline"
                disabled={saving || review.phase === "loading"}
                onClick={() => void reviewLatest()}
              >
                {uiText("library.reviewLatest")}
              </Button>
              {review.phase === "ready" && (
                <section aria-label={uiText("library.latestVersion")} className="space-y-3">
                  <h3 className="text-sm font-semibold">{uiText("library.latestVersion")}</h3>
                  <dl className="grid gap-2 text-sm">
                    {[
                      [
                        uiText("Enable SSO login"),
                        review.snapshot.oidc_enabled ? uiText("Enabled") : uiText("Disabled"),
                      ],
                      [uiText("Issuer URL"), review.snapshot.oidc_issuer_url],
                      [uiText("Client ID"), review.snapshot.oidc_client_id],
                      [
                        uiText("Client secret"),
                        review.snapshot.has_oidc_client_secret
                          ? uiText("Configured — enter to replace")
                          : uiText("Optional for public clients"),
                      ],
                      [uiText("Login button label"), review.snapshot.oidc_display_name],
                      [uiText("Scopes"), review.snapshot.oidc_scopes],
                      [uiText("Username claim"), review.snapshot.oidc_username_claim],
                      [uiText("Groups claim"), review.snapshot.oidc_groups_claim],
                      [uiText("Admin groups"), review.snapshot.oidc_admin_groups],
                      [uiText("Public callback URL override"), review.snapshot.oidc_redirect_uri],
                      [
                        uiText("Allow insecure HTTP issuer"),
                        review.snapshot.oidc_allow_insecure_http
                          ? uiText("Enabled")
                          : uiText("Disabled"),
                      ],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      disabled={saving || loading}
                      onClick={() => {
                        setDraft(oidcDraft(review.snapshot));
                        setBase(captureEditingBase(review.snapshot));
                        setEdited(false);
                        setClientSecret("");
                        setClearClientSecret(false);
                        setReview({ phase: "idle" });
                        generation.current += 1;
                      }}
                    >
                      {uiText("library.useLatest")}
                    </Button>
                    <Button
                      disabled={saving || loading}
                      onClick={() => void save(captureEditingBase(review.snapshot))}
                    >
                      {uiText("library.retryDraft")}
                    </Button>
                  </div>
                </section>
              )}
            </div>
          )}
          <label className="flex items-center justify-between gap-4 rounded-md border border-border bg-muted/40 p-3">
            <span>
              <span className="block text-sm font-medium text-foreground">
                {uiText("Enable SSO login")}
              </span>
              <span className="block text-xs text-muted-foreground">
                {uiText("Shows provider button on login page.")}
              </span>
            </span>
            <Checkbox
              checked={draft.oidc_enabled}
              onChange={(value) => set("oidc_enabled", value)}
              ariaLabel={uiText("Enable SSO login")}
              disabled={loading || saving || retired}
            />
          </label>

          <div className="grid gap-4 lg:grid-cols-2">
            <Field
              label={uiText("Issuer URL")}
              value={draft.oidc_issuer_url}
              onChange={(event) => set("oidc_issuer_url", event.target.value)}
              placeholder="https://auth.example.com/application/o/printstash"
              disabled={loading || saving || retired}
            />
            <Field
              label={uiText("Client ID")}
              value={draft.oidc_client_id}
              onChange={(event) => set("oidc_client_id", event.target.value)}
              placeholder={uiText("printstash")}
              disabled={loading || saving || retired}
            />
            <Field
              label={uiText("Client secret")}
              type="password"
              value={clientSecret}
              onChange={(event) => {
                generation.current += 1;
                if (!edited) {
                  setDraft(oidcDraft(config));
                  setBase(captureEditingBase(config));
                }
                setEdited(true);
                setClientSecret(event.target.value);
                setClearClientSecret(false);
              }}
              placeholder={
                hasClientSecret
                  ? uiText("Configured — enter to replace")
                  : uiText("Optional for public clients")
              }
              disabled={loading || saving || retired}
            />
            <Field
              label={uiText("Login button label")}
              value={draft.oidc_display_name}
              onChange={(event) => set("oidc_display_name", event.target.value)}
              placeholder="Authentik"
              disabled={loading || saving || retired}
            />
          </div>

          {hasClientSecret && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Checkbox
                checked={clearClientSecret}
                onChange={(value) => {
                  generation.current += 1;
                  if (!edited) {
                    setDraft(oidcDraft(config));
                    setBase(captureEditingBase(config));
                  }
                  setEdited(true);
                  setClearClientSecret(value);
                }}
                ariaLabel={uiText("Clear stored client secret")}
                disabled={loading || saving || retired}
              />
              {uiText("Clear stored client secret when saving")}
            </label>
          )}

          <details className="rounded-md border border-border bg-background">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {uiText("Advanced mapping")}
            </summary>
            <div className="grid gap-4 border-t border-border p-3 lg:grid-cols-2">
              <Field
                label={uiText("Scopes")}
                value={draft.oidc_scopes}
                onChange={(event) => set("oidc_scopes", event.target.value)}
                disabled={loading || saving || retired}
              />
              <Field
                label={uiText("Admin groups")}
                value={draft.oidc_admin_groups}
                onChange={(event) => set("oidc_admin_groups", event.target.value)}
                hint={uiText("Comma-separated group names granted superuser access.")}
                disabled={loading || saving || retired}
              />
              <Field
                label={uiText("Username claim")}
                value={draft.oidc_username_claim}
                onChange={(event) => set("oidc_username_claim", event.target.value)}
                disabled={loading || saving || retired}
              />
              <Field
                label={uiText("Groups claim")}
                value={draft.oidc_groups_claim}
                onChange={(event) => set("oidc_groups_claim", event.target.value)}
                disabled={loading || saving || retired}
              />
              <Field
                label={uiText("Public callback URL override")}
                value={draft.oidc_redirect_uri}
                onChange={(event) => set("oidc_redirect_uri", event.target.value)}
                hint={uiText("Leave blank unless reverse-proxy URL detection is incorrect.")}
                disabled={loading || saving || retired}
              />
              <label className="flex items-center gap-2 self-end pb-2 text-sm text-foreground">
                <Checkbox
                  checked={draft.oidc_allow_insecure_http}
                  onChange={(value) => set("oidc_allow_insecure_http", value)}
                  ariaLabel={uiText("Allow insecure HTTP issuer")}
                  disabled={loading || saving || retired}
                />
                {uiText("Allow HTTP issuer on trusted LAN")}
              </label>
            </div>
          </details>

          <div className="flex justify-end border-t border-border pt-4">
            <Button
              type="button"
              onClick={() => void save()}
              loading={saving}
              disabled={loading || retired || review.phase !== "idle"}
            >
              <KeyRound className="h-4 w-4" />
              {uiText("Save SSO settings")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </Localized>
  );
}
