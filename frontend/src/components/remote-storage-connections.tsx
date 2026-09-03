import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Cloud,
  Loader2,
  PauseCircle,
  PlayCircle,
  Plus,
  ShieldAlert,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input, inputClasses } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import {
  createStorageConnection,
  deleteStorageConnection,
  listStorageConnections,
  probeStorageConnection,
  updateStorageConnection,
} from "@/lib/api";
import type { StorageConnectionCreate } from "@/lib/api/storage-connections";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import type { LibrarySourceKind, StorageConnection, StorageConnectionPurpose } from "@/types";

type RemoteKind = Exclude<LibrarySourceKind, "mounted">;

const REMOTE_KINDS: readonly RemoteKind[] = ["s3", "webdav", "sftp", "gdrive"];
const PURPOSES: readonly StorageConnectionPurpose[] = ["library", "backup", "both"];
const FIELD_LABEL = "space-y-1.5 text-xs font-medium text-on-surface-variant";
const SELECT = cn(
  inputClasses,
  "bg-surface-container-lowest text-on-surface focus-visible:ring-ring",
);

function isRemoteKind(value: string): value is RemoteKind {
  return REMOTE_KINDS.some((kind) => kind === value);
}

function isPurpose(value: string): value is StorageConnectionPurpose {
  return PURPOSES.some((purpose) => purpose === value);
}

function purposeLabel(purpose: StorageConnectionPurpose): string {
  if (purpose === "library") return "Library sources";
  if (purpose === "backup") return "Backup replicas";
  return "Backups + libraries";
}

export function RemoteStorageConnections({ disabled = false }: { disabled?: boolean }) {
  const [connections, setConnections] = useState<StorageConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | "create" | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<RemoteKind>("s3");
  const [purpose, setPurpose] = useState<StorageConnectionPurpose>("both");
  const [root, setRoot] = useState("PrintStash");
  const [endpoint, setEndpoint] = useState("");
  const [bucket, setBucket] = useState("");
  const [region, setRegion] = useState("us-east-1");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [port, setPort] = useState(22);
  const [hostKey, setHostKey] = useState("");
  const [privateKeyPath, setPrivateKeyPath] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [removeTarget, setRemoveTarget] = useState<StorageConnection | null>(null);

  useEffect(() => {
    let active = true;
    void listStorageConnections()
      .then((rows) => {
        if (active) setConnections(rows);
      })
      .catch((error) => {
        if (active) toast.error(error);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function requestBody(): StorageConnectionCreate {
    const common = { name: name.trim(), kind, purpose };
    if (kind === "s3") {
      return {
        ...common,
        configuration: {
          provider: endpoint.trim() ? "s3_self_hosted" : "s3",
          bucket: bucket.trim(),
          endpoint_url: endpoint.trim(),
          region: region.trim(),
          addressing_style: endpoint.trim() ? "path" : "auto",
          root: root.trim(),
        },
        secrets: { access_key: accessKey, secret_key: secretKey },
      };
    }
    if (kind === "webdav") {
      return {
        ...common,
        configuration: {
          provider: "webdav",
          endpoint_url: endpoint.trim(),
          username: username.trim(),
          root: root.trim(),
        },
        secrets: { password },
      };
    }
    if (kind === "gdrive") {
      return {
        ...common,
        configuration: { client_id: clientId.trim(), root: root.trim() },
        secrets: { client_secret: clientSecret, refresh_token: refreshToken },
      };
    }
    return {
      ...common,
      configuration: {
        host: endpoint.trim(),
        port,
        username: username.trim(),
        host_key: hostKey.trim(),
        private_key_path: privateKeyPath.trim(),
        root: root.trim(),
      },
      secrets: { password, passphrase },
    };
  }

  function clearSecrets() {
    setPassword("");
    setPassphrase("");
    setClientSecret("");
    setRefreshToken("");
    setAccessKey("");
    setSecretKey("");
  }

  function canCreateConnection(): boolean {
    if (!name.trim() || !root.trim()) return false;
    if (kind === "s3") {
      return Boolean(bucket.trim() && accessKey.trim() && secretKey.trim());
    }
    if (kind === "webdav") {
      return Boolean(endpoint.trim() && username.trim() && password.trim());
    }
    if (kind === "gdrive") {
      return Boolean(clientId.trim() && clientSecret.trim() && refreshToken.trim());
    }
    return Boolean(
      endpoint.trim() &&
      username.trim() &&
      hostKey.trim() &&
      (password.trim() || privateKeyPath.trim()),
    );
  }

  async function addConnection() {
    if (!canCreateConnection()) return;
    setBusy("create");
    try {
      const created = await createStorageConnection(requestBody());
      setConnections((current) => [...current, created]);
      setName("");
      clearSecrets();
      toast.success("Remote storage connection saved.");
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  async function probe(connection: StorageConnection) {
    setBusy(connection.id);
    try {
      await probeStorageConnection(connection.id);
      toast.success(`${connection.name} is reachable.`);
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  async function toggle(connection: StorageConnection) {
    setBusy(connection.id);
    try {
      const updated = await updateStorageConnection(connection.id, {
        enabled: !connection.enabled,
      });
      setConnections((current) => current.map((row) => (row.id === updated.id ? updated : row)));
      toast.success(updated.enabled ? "Remote connection resumed." : "Remote connection paused.");
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  async function changePurpose(
    connection: StorageConnection,
    nextPurpose: StorageConnectionPurpose,
  ) {
    setBusy(connection.id);
    try {
      const updated = await updateStorageConnection(connection.id, { purpose: nextPurpose });
      setConnections((current) => current.map((row) => (row.id === updated.id ? updated : row)));
      toast.success(`${connection.name} will serve ${purposeLabel(nextPurpose).toLowerCase()}.`);
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  async function remove(connection: StorageConnection) {
    setBusy(connection.id);
    try {
      await deleteStorageConnection(connection.id);
      setConnections((current) => current.filter((row) => row.id !== connection.id));
      setRemoveTarget(null);
      toast.success("Remote storage connection removed.");
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Localized>
      <section className="overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-sm">
        <header className="flex items-start gap-3 border-b border-border px-4 py-4 sm:px-5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Cloud className="h-4 w-4" aria-hidden />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">Remote storage</h2>
            <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted-foreground">
              Connect a remote location once, then use it for off-site backup replicas, read-only
              Library sources, or both. Credentials remain encrypted on this server.
            </p>
          </div>
        </header>

        <div className="border-b border-border">
          <div className="px-4 py-3 sm:px-5">
            <h3 className="text-sm font-semibold text-foreground">Connections</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Pausing a connection stops new backup copies and library scans without forgetting its
              credentials.
            </p>
          </div>
          {loading ? (
            <p className="flex items-center gap-2 px-4 pb-4 text-sm text-muted-foreground sm:px-5">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading connections…
            </p>
          ) : connections.length === 0 ? (
            <p className="mx-4 mb-4 rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground sm:mx-5">
              No remote storage connected yet. Local backups and mounted Library sources continue to
              work normally.
            </p>
          ) : (
            <ul className="divide-y divide-border border-t border-border">
              {connections.map((connection) => (
                <li
                  key={connection.id}
                  className="grid gap-3 px-4 py-3 sm:px-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-medium text-foreground">
                        {connection.name}
                      </p>
                      <Badge variant="outline">{connection.kind.toUpperCase()}</Badge>
                      {connection.kind === "gdrive" && <Badge variant="secondary">Beta</Badge>}
                      <Badge variant={connection.enabled ? "secondary" : "outline"}>
                        {connection.enabled ? "Enabled" : "Paused"}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {connection.secret_fields_set.length} protected credential
                      {connection.secret_fields_set.length === 1 ? "" : "s"}
                    </p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[minmax(12rem,15rem)_auto] sm:items-end">
                    <label className={FIELD_LABEL}>
                      Use for
                      <select
                        className={SELECT}
                        aria-label={`Use ${connection.name} for`}
                        value={connection.purpose}
                        disabled={disabled || busy !== null}
                        onChange={(event) => {
                          if (isPurpose(event.target.value)) {
                            void changePurpose(connection, event.target.value);
                          }
                        }}
                      >
                        {PURPOSES.map((value) => (
                          <option key={value} value={value}>
                            {purposeLabel(value)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="flex flex-wrap gap-2 sm:justify-end">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={disabled || busy !== null || !connection.enabled}
                        onClick={() => void probe(connection)}
                      >
                        <CheckCircle2 className="h-4 w-4" aria-hidden /> Test
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={disabled || busy !== null}
                        onClick={() => void toggle(connection)}
                      >
                        {connection.enabled ? (
                          <PauseCircle className="h-4 w-4" aria-hidden />
                        ) : (
                          <PlayCircle className="h-4 w-4" aria-hidden />
                        )}
                        {connection.enabled ? "Pause" : "Resume"}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={disabled || busy !== null}
                        onClick={() => setRemoveTarget(connection)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden /> Remove
                      </Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-4 px-4 py-4 sm:px-5 sm:py-5">
          <div>
            <h3 className="text-sm font-semibold text-foreground">Add remote connection</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Choose the remote location and its allowed uses. You can change the uses later while
              nothing depends on them.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={FIELD_LABEL}>
              Connection name
              <Input
                value={name}
                maxLength={128}
                disabled={disabled}
                placeholder="Workshop storage"
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className={FIELD_LABEL}>
              Provider
              <select
                className={SELECT}
                value={kind}
                disabled={disabled}
                onChange={(event) => {
                  if (isRemoteKind(event.target.value)) setKind(event.target.value);
                }}
              >
                <option value="s3">S3 / compatible</option>
                <option value="webdav">WebDAV / Nextcloud</option>
                <option value="sftp">SFTP</option>
                <option value="gdrive">Google Drive (beta)</option>
              </select>
            </label>
            <label className={FIELD_LABEL}>
              Use for
              <select
                className={SELECT}
                value={purpose}
                disabled={disabled}
                onChange={(event) => {
                  if (isPurpose(event.target.value)) setPurpose(event.target.value);
                }}
              >
                {PURPOSES.map((value) => (
                  <option key={value} value={value}>
                    {purposeLabel(value)}
                  </option>
                ))}
              </select>
            </label>
            <label className={FIELD_LABEL}>
              Base folder
              <Input
                value={root}
                disabled={disabled}
                onChange={(event) => setRoot(event.target.value)}
              />
            </label>

            {kind === "s3" && (
              <>
                <label className={FIELD_LABEL}>
                  Bucket
                  <Input
                    value={bucket}
                    disabled={disabled}
                    onChange={(event) => setBucket(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Endpoint URL
                  <Input
                    value={endpoint}
                    disabled={disabled}
                    placeholder="Blank for AWS S3"
                    onChange={(event) => setEndpoint(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Region
                  <Input
                    value={region}
                    disabled={disabled}
                    onChange={(event) => setRegion(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Access key
                  <Input
                    value={accessKey}
                    disabled={disabled}
                    autoComplete="off"
                    onChange={(event) => setAccessKey(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Secret key
                  <Input
                    type="password"
                    value={secretKey}
                    disabled={disabled}
                    autoComplete="new-password"
                    onChange={(event) => setSecretKey(event.target.value)}
                  />
                </label>
              </>
            )}
            {kind === "webdav" && (
              <>
                <label className={FIELD_LABEL}>
                  WebDAV endpoint
                  <Input
                    value={endpoint}
                    disabled={disabled}
                    onChange={(event) => setEndpoint(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Username
                  <Input
                    value={username}
                    disabled={disabled}
                    onChange={(event) => setUsername(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Password
                  <Input
                    type="password"
                    value={password}
                    disabled={disabled}
                    autoComplete="new-password"
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </label>
              </>
            )}
            {kind === "sftp" && (
              <>
                <label className={FIELD_LABEL}>
                  SFTP host
                  <Input
                    value={endpoint}
                    disabled={disabled}
                    onChange={(event) => setEndpoint(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Port
                  <Input
                    type="number"
                    min={1}
                    max={65535}
                    value={port}
                    disabled={disabled}
                    onChange={(event) => setPort(Number(event.target.value))}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Username
                  <Input
                    value={username}
                    disabled={disabled}
                    onChange={(event) => setUsername(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Password
                  <Input
                    type="password"
                    value={password}
                    disabled={disabled}
                    autoComplete="new-password"
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </label>
                <label className={`${FIELD_LABEL} sm:col-span-2`}>
                  Pinned host key
                  <Input
                    value={hostKey}
                    disabled={disabled}
                    onChange={(event) => setHostKey(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Mounted private key path
                  <Input
                    value={privateKeyPath}
                    disabled={disabled}
                    placeholder="Use this or a password"
                    onChange={(event) => setPrivateKeyPath(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  Private key passphrase
                  <Input
                    type="password"
                    value={passphrase}
                    disabled={disabled}
                    autoComplete="new-password"
                    onChange={(event) => setPassphrase(event.target.value)}
                  />
                </label>
              </>
            )}
            {kind === "gdrive" && (
              <>
                <label className={FIELD_LABEL}>
                  OAuth client ID
                  <Input
                    value={clientId}
                    disabled={disabled}
                    onChange={(event) => setClientId(event.target.value)}
                  />
                </label>
                <label className={FIELD_LABEL}>
                  OAuth client secret
                  <Input
                    type="password"
                    value={clientSecret}
                    disabled={disabled}
                    autoComplete="new-password"
                    onChange={(event) => setClientSecret(event.target.value)}
                  />
                </label>
                <label className={`${FIELD_LABEL} sm:col-span-2`}>
                  Offline refresh token
                  <Input
                    type="password"
                    value={refreshToken}
                    disabled={disabled}
                    autoComplete="new-password"
                    onChange={(event) => setRefreshToken(event.target.value)}
                  />
                </label>
                <div className="flex gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-muted-foreground sm:col-span-2">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                  Google Drive backups are hash-verified on restore, but automatic retention does
                  not delete them and they never authorize Vault GC.
                </div>
              </>
            )}
          </div>
          {purpose === "both" && (
            <p className="rounded-md bg-muted p-3 text-xs leading-relaxed text-muted-foreground">
              Shared connections keep one base folder. Library source paths must stay separate from
              the reserved printstash-backups folder.
            </p>
          )}
          <div className="flex justify-end">
            <Button
              type="button"
              disabled={disabled || busy !== null || !canCreateConnection()}
              onClick={() => void addConnection()}
            >
              {busy === "create" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Plus className="h-4 w-4" aria-hidden />
              )}
              Save connection
            </Button>
          </div>
        </div>

        <ConfirmModal
          open={removeTarget !== null}
          onClose={() => setRemoveTarget(null)}
          onConfirm={() => {
            if (removeTarget) void remove(removeTarget);
          }}
          title="Remove remote connection?"
          description={
            removeTarget
              ? `“${removeTarget.name}” can only be removed when no Library source or owned backup still depends on it.`
              : ""
          }
          confirmLabel="Remove connection"
          busy={removeTarget !== null && busy === removeTarget.id}
        />
      </section>
    </Localized>
  );
}
