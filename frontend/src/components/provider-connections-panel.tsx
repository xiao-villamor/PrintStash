"use client";

import { currentLocale } from "@/lib/locale";

import { useEffect, useRef, useState } from "react";
import { KeyRound, Link, Pencil, Plus, Trash2 } from "lucide-react";

import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  authorizeMyMiniFactory,
  connectCults,
  createBrowserPairing,
  disconnectProvider,
  listBrowserDevices,
  listProviderConnections,
  renameBrowserDevice,
  revokeBrowserDevice,
} from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import {
  browserDevicesOptions,
  providerConnectionsOptions,
  useProviderConnectionCommands,
  type ProviderConnectionsTransport,
} from "@/lib/queries/settings-providers";
import { getSessionVersion } from "@/lib/session-transport";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError, userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type {
  BrowserDeviceRead,
  BrowserPairingCreateRead,
  CaptureProvider,
  ProviderConnectionRead,
} from "@/types";

export type ProviderConnectionsPanelDeps = ProviderConnectionsTransport & {
  navigate: (url: string) => void;
};

const defaultDeps: ProviderConnectionsPanelDeps = {
  listProviderConnections,
  authorizeMyMiniFactory,
  connectCults,
  disconnectProvider,
  createBrowserPairing,
  listBrowserDevices,
  renameBrowserDevice,
  revokeBrowserDevice,
  navigate: (url) => window.location.assign(url),
};

function connectionFor(
  connections: ProviderConnectionRead[],
  provider: CaptureProvider,
): ProviderConnectionRead {
  return (
    connections.find((connection) => connection.provider === provider) ?? {
      provider,
      connected: false,
      updated_at: null,
    }
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function ProviderConnectionsPanel({
  deps = defaultDeps,
}: {
  deps?: ProviderConnectionsPanelDeps;
}) {
  const { t } = useI18n();
  const connectionsQuery = useQuery(providerConnectionsOptions(deps));
  const devicesQuery = useQuery(browserDevicesOptions(deps));
  const commands = useProviderConnectionCommands(deps);
  const [pairing, setPairing] = useState<BrowserPairingCreateRead | null>(null);
  const [cultsUsername, setCultsUsername] = useState("");
  const [cultsPassword, setCultsPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [disconnectTarget, setDisconnectTarget] = useState<CaptureProvider | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<BrowserDeviceRead | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      setPairing(null);
      setCultsUsername("");
      setCultsPassword("");
      setBusy(null);
      setError("");
      setDisconnectTarget(null);
      setRevokeTarget(null);
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const current = (session: number) => live.current && session === getSessionVersion();

  async function startMyMiniFactory(): Promise<void> {
    const session = getSessionVersion();
    setBusy("myminifactory");
    setError("");
    try {
      const result = await commands.authorize(session);
      if (current(session)) deps.navigate(result.authorization_url);
    } catch (cause) {
      if (current(session)) setError(userMessage(cause));
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function submitCults(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const session = getSessionVersion();
    setBusy("cults");
    setError("");
    try {
      await commands.connect({ username: cultsUsername.trim(), password: cultsPassword }, session);
      if (current(session)) {
        setCultsUsername("");
        setCultsPassword("");
      }
    } catch (cause) {
      if (current(session)) setError(userMessage(cause));
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function createPairing(): Promise<void> {
    const session = getSessionVersion();
    setBusy("pairing");
    setError("");
    try {
      const result = await commands.pair(session);
      if (current(session)) setPairing(result);
    } catch (cause) {
      if (current(session)) setError(userMessage(cause));
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function saveDeviceName(device: BrowserDeviceRead, name: string): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed || trimmed === device.name) return;
    const session = getSessionVersion();
    setBusy(`rename-${device.id}`);
    setError("");
    try {
      await commands.rename(device.id, trimmed, session);
    } catch (cause) {
      if (current(session)) setError(userMessage(cause));
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function confirmDisconnect(): Promise<void> {
    if (!disconnectTarget) return;
    const session = getSessionVersion();
    setBusy(`disconnect-${disconnectTarget}`);
    setError("");
    try {
      await commands.disconnect(disconnectTarget, session);
      if (current(session)) setDisconnectTarget(null);
    } catch (cause) {
      if (current(session)) setError(userMessage(cause));
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function confirmRevoke(): Promise<void> {
    if (!revokeTarget) return;
    const session = getSessionVersion();
    setBusy(`revoke-${revokeTarget.id}`);
    setError("");
    try {
      await commands.revoke(revokeTarget.id, session);
      if (current(session)) setRevokeTarget(null);
    } catch (cause) {
      if (current(session)) setError(userMessage(cause));
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  const loadError = connectionsQuery.error ?? devicesQuery.error;
  const denied = [connectionsQuery.error, devicesQuery.error].some(
    (cause) => cause && [401, 403, 404].includes(parseApiError(cause).status),
  );
  const loadFailure = loadError && (
    <div className="space-y-3">
      <p role="alert">{t("settings.providerConnections.loadFailed")}</p>
      <Button
        onClick={() => {
          void connectionsQuery.refetch();
          void devicesQuery.refetch();
        }}
      >
        {t("Retry")}
      </Button>
    </div>
  );
  if (denied || !connectionsQuery.data || !devicesQuery.data)
    return loadFailure || <p role="status">{t("Loading…")}</p>;
  const connections = connectionsQuery.data;
  const devices = devicesQuery.data;

  const mmf = connectionFor(connections, "myminifactory");
  const cults = connectionFor(connections, "cults");

  return (
    <div className="space-y-3">
      {loadFailure}
      <fieldset disabled={busy !== null || Boolean(loadError)} className="min-w-0 space-y-6">
        <section
          aria-labelledby="provider-connections-title"
          className="overflow-hidden rounded border border-border bg-card"
        >
          <div className="border-b border-border px-4 py-4 sm:px-5">
            <h2 id="provider-connections-title" className="text-sm font-semibold text-foreground">
              {t("settings.providerConnections.title")}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("settings.providerConnections.description")}
            </p>
          </div>
          <div className="space-y-4 p-4 sm:p-5">
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="rounded border border-border p-3">
              <ConnectionHeader
                label={t("settings.providerConnections.mmf")}
                connected={mmf.connected}
                t={t}
              />
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                {t("settings.providerConnections.mmfDescription")}
              </p>
              <div className="mt-3">
                {mmf.connected ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setError("");
                      setDisconnectTarget("myminifactory");
                    }}
                  >
                    {t("settings.providerConnections.disconnect")}
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={() => void startMyMiniFactory()}
                    loading={busy === "myminifactory"}
                  >
                    <Link className="h-3.5 w-3.5" />
                    {t("settings.providerConnections.connectMmf")}
                  </Button>
                )}
              </div>
            </div>
            <form
              className="rounded border border-border p-3"
              onSubmit={(event) => void submitCults(event)}
            >
              <ConnectionHeader
                label={t("settings.providerConnections.cults")}
                connected={cults.connected}
                t={t}
              />
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                {t("settings.providerConnections.cultsDescription")}
              </p>
              {!cults.connected && (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <Input
                    aria-label={t("settings.providerConnections.cultsUsername")}
                    value={cultsUsername}
                    onChange={(event) => setCultsUsername(event.target.value)}
                    autoComplete="username"
                    maxLength={255}
                    placeholder={t("settings.providerConnections.cultsUsername")}
                    required
                  />
                  <Input
                    aria-label={t("settings.providerConnections.cultsPassword")}
                    value={cultsPassword}
                    onChange={(event) => setCultsPassword(event.target.value)}
                    type="password"
                    autoComplete="current-password"
                    maxLength={1024}
                    placeholder={t("settings.providerConnections.cultsPassword")}
                    required
                  />
                </div>
              )}
              <div className="mt-3">
                {cults.connected ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setError("");
                      setDisconnectTarget("cults");
                    }}
                  >
                    {t("settings.providerConnections.disconnect")}
                  </Button>
                ) : (
                  <Button type="submit" size="sm" loading={busy === "cults"}>
                    <Link className="h-3.5 w-3.5" />
                    {t("settings.providerConnections.connectCults")}
                  </Button>
                )}
              </div>
            </form>
          </div>
        </section>

        <section
          aria-labelledby="paired-browsers-title"
          className="overflow-hidden rounded border border-border bg-card"
        >
          <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-4 sm:px-5">
            <div>
              <h2 id="paired-browsers-title" className="text-sm font-semibold text-foreground">
                {t("settings.pairedBrowsers.title")}
              </h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t("settings.pairedBrowsers.description")}
              </p>
            </div>
            <Button size="sm" onClick={() => void createPairing()} loading={busy === "pairing"}>
              <Plus className="h-3.5 w-3.5" />
              {t("settings.pairedBrowsers.createCode")}
            </Button>
          </div>
          <div className="space-y-4 p-4 sm:p-5">
            {pairing && (
              <div className="rounded border border-primary/40 bg-primary-soft p-3" role="status">
                <p className="text-xs text-muted-foreground">
                  {t("settings.pairedBrowsers.codeHelp")}
                </p>
                <code className="mt-2 block select-all break-all rounded bg-background px-3 py-2 font-mono text-sm text-foreground">
                  {pairing.code}
                </code>
                <p className="mt-2 text-xs text-muted-foreground">
                  {t("settings.pairedBrowsers.expiresAt", {
                    expiresAt: formatDate(pairing.expires_at),
                  })}
                </p>
              </div>
            )}
            {devices.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("settings.pairedBrowsers.empty")}</p>
            ) : (
              <div className="space-y-2">
                {devices.map((device) => (
                  <BrowserDeviceRow
                    key={device.id}
                    device={device}
                    busy={busy}
                    onSave={saveDeviceName}
                    onRevoke={(device) => {
                      setError("");
                      setRevokeTarget(device);
                    }}
                    labels={{
                      name: t("settings.pairedBrowsers.name", { name: device.name }),
                      save: t("settings.pairedBrowsers.saveName"),
                      revoke: t("settings.pairedBrowsers.revoke", { name: device.name }),
                      revoked: t("settings.pairedBrowsers.revoked"),
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </section>

        <ConfirmModal
          open={disconnectTarget !== null}
          onClose={() => setDisconnectTarget(null)}
          onConfirm={() => void confirmDisconnect()}
          title={t("settings.providerConnections.disconnectTitle")}
          description={[t("settings.providerConnections.disconnectDescription"), error]
            .filter(Boolean)
            .join(" ")}
          confirmLabel={t("settings.providerConnections.disconnect")}
          busy={busy?.startsWith("disconnect-") ?? false}
        />
        <ConfirmModal
          open={revokeTarget !== null}
          onClose={() => setRevokeTarget(null)}
          onConfirm={() => void confirmRevoke()}
          title={t("settings.pairedBrowsers.revokeTitle")}
          description={[t("settings.pairedBrowsers.revokeDescription"), error]
            .filter(Boolean)
            .join(" ")}
          confirmLabel={t("settings.pairedBrowsers.revokeConfirm")}
          busy={busy?.startsWith("revoke-") ?? false}
        />
      </fieldset>
    </div>
  );
}

function ConnectionHeader({
  label,
  connected,
  t,
}: {
  label: string;
  connected: boolean;
  t: ReturnType<typeof useI18n>["t"];
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <p className="text-sm font-medium text-foreground">{label}</p>
      <Badge
        variant={connected ? "success" : "secondary"}
        className="font-mono text-3xs uppercase tracking-wider"
      >
        {connected
          ? t("settings.providerConnections.connected")
          : t("settings.providerConnections.notConnected")}
      </Badge>
    </div>
  );
}

function BrowserDeviceRow({
  device,
  busy,
  onSave,
  onRevoke,
  labels,
}: {
  device: BrowserDeviceRead;
  busy: string | null;
  onSave: (device: BrowserDeviceRead, name: string) => Promise<void>;
  onRevoke: (device: BrowserDeviceRead) => void;
  labels: { name: string; save: string; revoke: string; revoked: string };
}) {
  const nameRef = useRef<HTMLInputElement>(null);
  const revoked = device.revoked_at !== null;
  return (
    <div className="flex flex-col gap-2 rounded border border-border p-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <Input
            aria-label={labels.name}
            ref={nameRef}
            defaultValue={device.name}
            disabled={revoked || busy === `rename-${device.id}`}
            maxLength={128}
          />
        </div>
        {revoked && <p className="mt-1 text-xs text-muted-foreground">{labels.revoked}</p>}
      </div>
      {!revoked && (
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void onSave(device, nameRef.current?.value ?? device.name)}
            loading={busy === `rename-${device.id}`}
            aria-label={labels.save}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => onRevoke(device)}
            aria-label={labels.revoke}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}
