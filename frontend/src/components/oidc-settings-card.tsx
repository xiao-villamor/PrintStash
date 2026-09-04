"use client";

import { useEffect, useState } from "react";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { getVaultConfig, updateVaultConfig } from "@/lib/api";
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

const EMPTY: OidcDraft = {
  oidc_enabled: false,
  oidc_issuer_url: "",
  oidc_client_id: "",
  oidc_scopes: "openid profile email groups",
  oidc_username_claim: "preferred_username",
  oidc_groups_claim: "groups",
  oidc_admin_groups: "printstash-admins",
  oidc_display_name: "Single sign-on",
  oidc_redirect_uri: "",
  oidc_allow_insecure_http: false,
};

function Field({
  label,
  hint,
  ...props
}: React.ComponentProps<typeof Input> & { label: string; hint?: string }) {
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

/**
 * The two collaborators default to the real API layer, so callers render
 * `<OidcSettingsCard />` unchanged. They are props so a test can drive the card
 * with an in-memory config instead of reaching through its imports.
 */
export function OidcSettingsCard({
  loadConfig = getVaultConfig,
  saveConfig = updateVaultConfig,
}: {
  loadConfig?: () => Promise<OidcConfig>;
  saveConfig?: (payload: OidcConfigUpdate) => Promise<OidcConfig>;
} = {}) {
  const [draft, setDraft] = useState<OidcDraft>(EMPTY);
  const [clientSecret, setClientSecret] = useState("");
  const [hasClientSecret, setHasClientSecret] = useState(false);
  const [clearClientSecret, setClearClientSecret] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    loadConfig()
      .then((config) => {
        if (!alive) return;
        setDraft({
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
        });
        setHasClientSecret(config.has_oidc_client_secret);
      })
      .catch(toast.error)
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [loadConfig]);

  function set<K extends keyof OidcDraft>(key: K, value: OidcDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    if (draft.oidc_enabled && (!draft.oidc_issuer_url.trim() || !draft.oidc_client_id.trim())) {
      toast.error("Issuer URL and client ID are required before enabling SSO.");
      return;
    }
    setSaving(true);
    try {
      const payload: OidcConfigUpdate = { ...draft };
      if (clientSecret) payload.oidc_client_secret = clientSecret;
      else if (clearClientSecret) payload.oidc_client_secret = "";
      const config = await saveConfig(payload);
      setHasClientSecret(config.has_oidc_client_secret);
      setClientSecret("");
      setClearClientSecret(false);
      toast.success("Single sign-on settings saved.");
    } catch (error) {
      toast.error(error);
    } finally {
      setSaving(false);
    }
  }

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
                <CardTitle className="text-sm">OpenID Connect</CardTitle>
                <CardDescription className="mt-1 text-xs">
                  Connect Authentik, Authelia, Keycloak, or another standards-compatible identity
                  provider. Local login stays available.
                </CardDescription>
              </div>
            </div>
            {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          </div>
        </CardHeader>
        <CardContent className="space-y-5 p-4 sm:p-5">
          <label className="flex items-center justify-between gap-4 rounded-md border border-border bg-muted/40 p-3">
            <span>
              <span className="block text-sm font-medium text-foreground">Enable SSO login</span>
              <span className="block text-xs text-muted-foreground">
                Shows provider button on login page.
              </span>
            </span>
            <Checkbox
              checked={draft.oidc_enabled}
              onChange={(value) => set("oidc_enabled", value)}
              ariaLabel="Enable SSO login"
              disabled={loading || saving}
            />
          </label>

          <div className="grid gap-4 lg:grid-cols-2">
            <Field
              label="Issuer URL"
              value={draft.oidc_issuer_url}
              onChange={(event) => set("oidc_issuer_url", event.target.value)}
              placeholder="https://auth.example.com/application/o/printstash"
              disabled={loading || saving}
            />
            <Field
              label="Client ID"
              value={draft.oidc_client_id}
              onChange={(event) => set("oidc_client_id", event.target.value)}
              placeholder="printstash"
              disabled={loading || saving}
            />
            <Field
              label="Client secret"
              type="password"
              value={clientSecret}
              onChange={(event) => {
                setClientSecret(event.target.value);
                setClearClientSecret(false);
              }}
              placeholder={
                hasClientSecret ? "Configured — enter to replace" : "Optional for public clients"
              }
              disabled={loading || saving}
            />
            <Field
              label="Login button label"
              value={draft.oidc_display_name}
              onChange={(event) => set("oidc_display_name", event.target.value)}
              placeholder="Authentik"
              disabled={loading || saving}
            />
          </div>

          {hasClientSecret && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Checkbox
                checked={clearClientSecret}
                onChange={setClearClientSecret}
                ariaLabel="Clear stored client secret"
                disabled={saving}
              />
              Clear stored client secret when saving
            </label>
          )}

          <details className="rounded-md border border-border bg-background">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              Advanced mapping
            </summary>
            <div className="grid gap-4 border-t border-border p-3 lg:grid-cols-2">
              <Field
                label="Scopes"
                value={draft.oidc_scopes}
                onChange={(event) => set("oidc_scopes", event.target.value)}
                disabled={saving}
              />
              <Field
                label="Admin groups"
                value={draft.oidc_admin_groups}
                onChange={(event) => set("oidc_admin_groups", event.target.value)}
                hint="Comma-separated group names granted superuser access."
                disabled={saving}
              />
              <Field
                label="Username claim"
                value={draft.oidc_username_claim}
                onChange={(event) => set("oidc_username_claim", event.target.value)}
                disabled={saving}
              />
              <Field
                label="Groups claim"
                value={draft.oidc_groups_claim}
                onChange={(event) => set("oidc_groups_claim", event.target.value)}
                disabled={saving}
              />
              <Field
                label="Public callback URL override"
                value={draft.oidc_redirect_uri}
                onChange={(event) => set("oidc_redirect_uri", event.target.value)}
                hint="Leave blank unless reverse-proxy URL detection is incorrect."
                disabled={saving}
              />
              <label className="flex items-center gap-2 self-end pb-2 text-sm text-foreground">
                <Checkbox
                  checked={draft.oidc_allow_insecure_http}
                  onChange={(value) => set("oidc_allow_insecure_http", value)}
                  ariaLabel="Allow insecure HTTP issuer"
                  disabled={saving}
                />
                Allow HTTP issuer on trusted LAN
              </label>
            </div>
          </details>

          <div className="flex justify-end border-t border-border pt-4">
            <Button type="button" onClick={save} loading={saving} disabled={loading}>
              <KeyRound className="h-4 w-4" />
              Save SSO settings
            </Button>
          </div>
        </CardContent>
      </Card>
    </Localized>
  );
}
