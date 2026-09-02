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
import { Input } from "@/components/ui/input";
import {
  createStorageConnection,
  deleteStorageConnection,
  listStorageConnections,
  probeStorageConnection,
  updateStorageConnection,
} from "@/lib/api";
import type { StorageConnectionCreate } from "@/lib/api/storage-connections";
import { toast } from "@/lib/toast";
import type { LibrarySourceKind, StorageConnection } from "@/types";

type RemoteKind = Exclude<LibrarySourceKind, "mounted">;

const FIELD_LABEL = "space-y-1.5 text-xs font-medium text-on-surface-variant";
const REMOTE_KINDS: readonly RemoteKind[] = ["s3", "webdav", "sftp", "gdrive"];

function isRemoteKind(value: string): value is RemoteKind {
  return REMOTE_KINDS.some((kind) => kind === value);
}

export function BackupDestinations({ disabled = false }: { disabled?: boolean }) {
  const [connections, setConnections] = useState<StorageConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | "create" | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<RemoteKind>("s3");
  const [root, setRoot] = useState("PrintStash/backups");
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
        if (active) setConnections(rows.filter((row) => row.purpose === "backup"));
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
    const common = { name: name.trim(), kind, purpose: "backup" as const };
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

  async function addDestination() {
    if (!name.trim() || !root.trim()) return;
    setBusy("create");
    try {
      const created = await createStorageConnection(requestBody());
      setConnections((current) => [...current, created]);
      setName("");
      setPassword("");
      setPassphrase("");
      setClientSecret("");
      setRefreshToken("");
      setAccessKey("");
      setSecretKey("");
      const result = await probeStorageConnection(created.id);
      toast.success(result.ok ? "Backup destination connected." : "Destination saved.");
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
      setConnections((current) => current.map((row) => (row.id === connection.id ? updated : row)));
      toast.success(updated.enabled ? "Backup destination resumed." : "Backup destination paused.");
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
      toast.success("Backup destination removed.");
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-4 border-t border-outline-variant pt-4">
      <div className="space-y-1">
        <h4 className="flex items-center gap-2 text-sm font-semibold text-on-surface">
          <Cloud className="h-4 w-4" aria-hidden /> Remote backup replicas
        </h4>
        <p className="max-w-3xl text-sm text-on-surface-variant">
          Each backup is committed locally first, then copied to every enabled destination. Use a
          dedicated folder that is not also indexed as a Library source.
        </p>
      </div>

      {loading ? (
        <p className="flex items-center gap-2 text-sm text-on-surface-variant">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading destinations…
        </p>
      ) : connections.length === 0 ? (
        <p className="rounded-lg border border-dashed border-outline-variant p-4 text-sm text-on-surface-variant">
          No remote replicas configured. Local backups continue to work normally.
        </p>
      ) : (
        <ul className="divide-y divide-outline-variant rounded-lg border border-outline-variant">
          {connections.map((connection) => (
            <li
              key={connection.id}
              className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-on-surface">{connection.name}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{connection.kind.toUpperCase()}</Badge>
                  {connection.kind === "gdrive" && <Badge variant="secondary">Beta</Badge>}
                  <Badge variant={connection.enabled ? "secondary" : "outline"}>
                    {connection.enabled ? "Enabled" : "Paused"}
                  </Badge>
                  <span className="text-xs text-on-surface-variant">
                    {connection.secret_fields_set.length} protected credential
                    {connection.secret_fields_set.length === 1 ? "" : "s"}
                  </span>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
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
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-4 rounded-lg bg-surface-container-low p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className={FIELD_LABEL}>
            Destination name
            <Input
              value={name}
              maxLength={128}
              disabled={disabled}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className={FIELD_LABEL}>
            Provider
            <select
              className="h-10 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 text-base text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
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

          {kind === "s3" && (
            <>
              <label className={FIELD_LABEL}>
                Backup bucket
                <Input
                  value={bucket}
                  disabled={disabled}
                  onChange={(e) => setBucket(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Endpoint URL
                <Input
                  value={endpoint}
                  disabled={disabled}
                  placeholder="Blank for AWS S3"
                  onChange={(e) => setEndpoint(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Region
                <Input
                  value={region}
                  disabled={disabled}
                  onChange={(e) => setRegion(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Access key
                <Input
                  value={accessKey}
                  disabled={disabled}
                  autoComplete="off"
                  onChange={(e) => setAccessKey(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Secret key
                <Input
                  type="password"
                  value={secretKey}
                  disabled={disabled}
                  autoComplete="new-password"
                  onChange={(e) => setSecretKey(e.target.value)}
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
                  onChange={(e) => setEndpoint(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Username
                <Input
                  value={username}
                  disabled={disabled}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Password
                <Input
                  type="password"
                  value={password}
                  disabled={disabled}
                  autoComplete="new-password"
                  onChange={(e) => setPassword(e.target.value)}
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
                  onChange={(e) => setEndpoint(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Username
                <Input
                  value={username}
                  disabled={disabled}
                  onChange={(e) => setUsername(e.target.value)}
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
                  onChange={(e) => setPort(Number(e.target.value))}
                />
              </label>
              <label className={FIELD_LABEL}>
                Password
                <Input
                  type="password"
                  value={password}
                  disabled={disabled}
                  autoComplete="new-password"
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <label className={`${FIELD_LABEL} sm:col-span-2`}>
                Pinned host key
                <Input
                  value={hostKey}
                  disabled={disabled}
                  onChange={(e) => setHostKey(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Mounted private key path
                <Input
                  value={privateKeyPath}
                  disabled={disabled}
                  placeholder="Use this or a password"
                  onChange={(e) => setPrivateKeyPath(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                Private key passphrase
                <Input
                  type="password"
                  value={passphrase}
                  disabled={disabled}
                  autoComplete="new-password"
                  onChange={(e) => setPassphrase(e.target.value)}
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
                  onChange={(e) => setClientId(e.target.value)}
                />
              </label>
              <label className={FIELD_LABEL}>
                OAuth client secret
                <Input
                  type="password"
                  value={clientSecret}
                  disabled={disabled}
                  autoComplete="new-password"
                  onChange={(e) => setClientSecret(e.target.value)}
                />
              </label>
              <label className={`${FIELD_LABEL} sm:col-span-2`}>
                Offline refresh token
                <Input
                  type="password"
                  value={refreshToken}
                  disabled={disabled}
                  autoComplete="new-password"
                  onChange={(e) => setRefreshToken(e.target.value)}
                />
              </label>
              <div className="flex gap-2 rounded-lg border border-warning/30 bg-warning/10 p-3 text-xs text-on-surface-variant sm:col-span-2">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                Google Drive replicas are fully hash-verified on restore, but they are not deleted
                by automatic retention and never authorize Vault GC.
              </div>
            </>
          )}
          <label className={`${FIELD_LABEL} sm:col-span-2`}>
            Dedicated root folder
            <Input value={root} disabled={disabled} onChange={(e) => setRoot(e.target.value)} />
          </label>
        </div>
        <div className="flex justify-end">
          <Button
            type="button"
            disabled={disabled || busy !== null || !name.trim() || !root.trim()}
            onClick={() => void addDestination()}
          >
            {busy === "create" ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Plus className="h-4 w-4" aria-hidden />
            )}
            Save and test destination
          </Button>
        </div>
      </div>
      <ConfirmModal
        open={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        onConfirm={() => {
          if (removeTarget) void remove(removeTarget);
        }}
        title="Remove backup destination?"
        description={
          removeTarget
            ? `PrintStash will stop creating new replicas in “${removeTarget.name}”. Existing owned backups prevent removal until they are handled.`
            : ""
        }
        confirmLabel="Remove destination"
        busy={removeTarget !== null && busy === removeTarget.id}
      />
    </section>
  );
}
