"use client";

import { SettingsGroupHead } from "@/components/settings-card";
import { currentLocale } from "@/lib/locale";

import { useEffect, useRef, useState } from "react";
import { CloudUpload, ExternalLink } from "lucide-react";

import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Modal } from "@/components/ui/modal";
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
import { captureEditingBase } from "@/lib/api/editing";
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
  const [cultsOpen, setCultsOpen] = useState(false);
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
      setCultsOpen(false);
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
        setCultsOpen(false);
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
  async function saveDeviceName(
    device: BrowserDeviceRead,
    name: string,
  ): Promise<BrowserDeviceRead> {
    const session = getSessionVersion();
    setBusy(`rename-${device.id}`);
    try {
      return await commands.rename(device.id, name, captureEditingBase(device), session);
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function reviewDevice(id: number): Promise<BrowserDeviceRead> {
    const session = getSessionVersion();
    setBusy(`review-${id}`);
    try {
      return await commands.reviewDevice(id, session);
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
        size="sm"
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
      <fieldset disabled={busy !== null || Boolean(loadError)} className="min-w-0">
        <section
          aria-labelledby="provider-connections-title"
          className="overflow-hidden rounded border border-border bg-card"
        >
          <SettingsGroupHead
            icon={CloudUpload}
            level={2}
            id="provider-connections-title"
            title={t("settings.providerConnections.title")}
            description={t("settings.providerConnections.description")}
            className="settings-card-head--flush"
          />
          <div className="space-y-0 px-4 pb-4 sm:pl-16 sm:pr-5">
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="settings-list-row">
              <ConnectionHeader
                label={t("settings.providerConnections.mmf")}
                description={t("settings.providerConnections.mmfDescription")}
              />
              <div>
                <ConnectionStatus connected={mmf.connected} t={t} />
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
                    variant="outline"
                    size="sm"
                    onClick={() => void startMyMiniFactory()}
                    loading={busy === "myminifactory"}
                  >
                    {t("settings.providerConnections.connectMmf")}
                  </Button>
                )}
              </div>
            </div>
            <div className="settings-list-row">
              <ConnectionHeader
                label={t("settings.providerConnections.cults")}
                description={t("settings.providerConnections.cultsDescription")}
              />
              <div>
                <ConnectionStatus connected={cults.connected} t={t} />
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
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setCultsOpen(true)}
                  >
                    {t("settings.providerConnections.connectCults")}
                  </Button>
                )}
              </div>
              <Modal
                open={cultsOpen && !cults.connected}
                onClose={() => {
                  if (busy !== "cults") setCultsOpen(false);
                }}
                title={t("settings.providerConnections.connectCults")}
              >
                <form className="space-y-4" onSubmit={(event) => void submitCults(event)}>
                  <fieldset disabled={busy !== null || Boolean(loadError)} className="space-y-3">
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
                    {error && (
                      <p role="alert" className="text-sm text-destructive">
                        {error}
                      </p>
                    )}
                    <div className="flex justify-end">
                      <Button type="submit" size="sm" loading={busy === "cults"}>
                        {t("settings.providerConnections.connectCults")}
                      </Button>
                    </div>
                  </fieldset>
                </form>
              </Modal>
            </div>
          </div>
        </section>

        <section
          aria-labelledby="paired-browsers-title"
          className="overflow-hidden rounded border border-border bg-card"
        >
          <SettingsGroupHead
            icon={ExternalLink}
            level={2}
            id="paired-browsers-title"
            title={t("settings.pairedBrowsers.title")}
            description={t("settings.pairedBrowsers.description")}
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => void createPairing()}
                loading={busy === "pairing"}
              >
                {t("settings.pairedBrowsers.createCode")}
              </Button>
            }
            className="settings-card-head--flush"
          />
          <div className="space-y-4 px-4 pb-4 sm:pl-16 sm:pr-5">
            {pairing && (
              <div className="border border-border bg-card p-3" role="status">
                <p className="text-xs text-muted-foreground">
                  {t("settings.pairedBrowsers.codeHelp")}
                </p>
                <code className="mt-2 block select-all break-all border border-border bg-background px-3 py-2 font-mono text-sm text-foreground">
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
              <div>
                {devices.map((device) => (
                  <BrowserDeviceRow
                    key={device.id}
                    device={device}
                    busy={busy}
                    onSave={saveDeviceName}
                    onReview={reviewDevice}
                    onRevoke={(device) => {
                      setError("");
                      setRevokeTarget(device);
                    }}
                    labels={{
                      name: t("settings.pairedBrowsers.name", { name: device.name }),
                      rename: t("settings.pairedBrowsers.rename", { name: device.name }),
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

function ConnectionHeader({ label, description }: { label: string; description: string }) {
  return (
    <div>
      <p className="settings-list-title">{label}</p>
      <p className="settings-list-meta">{description}</p>
    </div>
  );
}

function ConnectionStatus({
  connected,
  t,
}: {
  connected: boolean;
  t: ReturnType<typeof useI18n>["t"];
}) {
  return (
    <span className="settings-status">
      {connected
        ? t("settings.providerConnections.connected")
        : t("settings.providerConnections.notConnected")}
    </span>
  );
}

type BrowserReview =
  | { phase: "idle" }
  | { phase: "required" }
  | { phase: "ready"; snapshot: BrowserDeviceRead }
  | { phase: "denied" };

function BrowserDeviceRow({
  device,
  busy,
  onSave,
  onReview,
  onRevoke,
  labels,
}: {
  device: BrowserDeviceRead;
  busy: string | null;
  onSave: (device: BrowserDeviceRead, name: string) => Promise<BrowserDeviceRead>;
  onReview: (id: number) => Promise<BrowserDeviceRead>;
  onRevoke: (device: BrowserDeviceRead) => void;
  labels: { name: string; rename: string; save: string; revoke: string; revoked: string };
}) {
  const { t } = useI18n();
  // No copied server list: this snapshot exists only after a deliberate edit/adoption.
  const [draft, setDraft] = useState<{ base: BrowserDeviceRead; name: string } | null>(null);
  const [review, setReview] = useState<BrowserReview>({ phase: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      setRenaming(false);
      setDraft(null);
      setReview({ phase: "idle" });
      setError(null);
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const current = (session: number) => live.current && session === getSessionVersion();
  const revoked = device.revoked_at !== null || draft?.base.revoked_at != null;
  const name = draft?.name ?? device.name;
  const snapshot = review.phase === "ready" ? review.snapshot : null;
  const replaced = snapshot !== null && snapshot.edit_epoch !== (draft?.base ?? device).edit_epoch;
  const edited = name.trim() !== (draft?.base ?? device).name;

  function failure(cause: unknown) {
    if ([401, 403, 404].includes(parseApiError(cause).status)) {
      setDraft(null);
      setReview({ phase: "denied" });
    } else setReview({ phase: "required" });
    setError(userMessage(cause));
  }
  async function save(revised: boolean) {
    if (!draft || !edited || busy !== null || !name.trim()) return;
    if (revised ? !snapshot || replaced || snapshot.revoked_at !== null : review.phase !== "idle")
      return;
    const session = getSessionVersion();
    setError(null);
    try {
      await onSave(revised && snapshot ? snapshot : draft.base, name.trim());
      if (current(session)) {
        setRenaming(false);
        setDraft(null);
        setReview({ phase: "idle" });
      }
    } catch (cause) {
      if (current(session)) failure(cause);
    }
  }
  async function reviewCurrent() {
    const session = getSessionVersion();
    setError(null);
    setReview({ phase: "required" });
    try {
      const next = await onReview(device.id);
      if (current(session)) setReview({ phase: "ready", snapshot: next });
    } catch (cause) {
      if (current(session)) failure(cause);
    }
  }
  if (review.phase === "denied") return <p role="alert">{error}</p>;
  const editing = !revoked && (renaming || draft !== null || review.phase !== "idle");
  return (
    <div className="settings-list-item space-y-3">
      {editing ? (
        <form
          className="settings-list-row"
          onSubmit={(event) => {
            event.preventDefault();
            void save(false);
          }}
        >
          <Input
            aria-label={labels.name}
            value={name}
            onChange={(event) =>
              setDraft({ base: draft?.base ?? device, name: event.target.value })
            }
            disabled={busy !== null}
            maxLength={128}
            required
            pattern={".*\\S.*"}
            autoFocus
            className="max-w-sm"
          />
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => {
                setRenaming(false);
                setDraft(null);
                setReview({ phase: "idle" });
                setError(null);
              }}
            >
              {t("Cancel")}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!edited || review.phase !== "idle" || busy !== null}
              loading={busy === `rename-${device.id}`}
              aria-label={labels.save}
            >
              {t("Save")}
            </Button>
          </div>
        </form>
      ) : (
        <div className="settings-list-row">
          <div>
            <p className="settings-list-title">{device.name}</p>
            <p className="settings-list-meta">
              {[
                t("settings.pairedBrowsers.pairedOn", { date: formatDate(device.created_at) }),
                device.last_used_at
                  ? t("settings.pairedBrowsers.lastUsed", {
                      date: formatDate(device.last_used_at),
                    })
                  : t("settings.pairedBrowsers.neverUsed"),
              ].join(" · ")}
            </p>
          </div>
          <div>
            {revoked ? (
              <span className="settings-status">{labels.revoked}</span>
            ) : (
              <>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => setRenaming(true)}
                  aria-label={labels.rename}
                >
                  {t("Rename")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="text-destructive"
                  onClick={() => onRevoke(device)}
                  aria-label={labels.revoke}
                >
                  {t("settings.pairedBrowsers.revokeAction")}
                </Button>
              </>
            )}
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {review.phase !== "idle" && (
        <div role="status" className="space-y-2 text-sm">
          <p>{t("settings.pairedBrowsers.reviewRequired")}</p>
          {snapshot && <p>{t("settings.pairedBrowsers.currentName", { name: snapshot.name })}</p>}
          {replaced && <p>{t("settings.pairedBrowsers.replaced")}</p>}
          {snapshot?.revoked_at && <p>{labels.revoked}</p>}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy !== null}
              onClick={() => void reviewCurrent()}
            >
              {t("Review current values")}
            </Button>
            {snapshot && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy !== null}
                  onClick={() => {
                    setDraft({ base: snapshot, name: snapshot.name });
                    setReview({ phase: "idle" });
                    setError(null);
                  }}
                >
                  {t("Use current values")}
                </Button>
                <Button
                  size="sm"
                  disabled={
                    busy !== null ||
                    replaced ||
                    snapshot.revoked_at !== null ||
                    !edited ||
                    !name.trim()
                  }
                  onClick={() => void save(true)}
                >
                  {t("Save revised changes")}
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
