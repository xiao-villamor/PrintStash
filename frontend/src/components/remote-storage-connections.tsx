import { useEffect, useState } from "react";
import { CheckCircle2, Cloud, Loader2, PauseCircle, PlayCircle, Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input, inputClasses } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import {
  createStorageConnection,
  deleteStorageConnection,
  listStorageConnections,
  getStorageProviders,
  probeStorageConnection,
  updateStorageConnection,
} from "@/lib/api";
import { StorageProviderFields } from "@/components/storage-provider-fields";
import {
  providerDefaults,
  providerFields,
  providerFormError,
  splitProviderValues,
} from "@/lib/storage-provider-form";
import type { StorageConnectionCreate } from "@/lib/api/storage-connections";
import { toast } from "@/lib/toast";
import { useOptionalI18n } from "@/lib/i18n";
import { storageOperationMessage } from "@/lib/storage-operations";
import { cn } from "@/lib/utils";
import type {
  LibrarySourceKind,
  StorageConnection,
  StorageConnectionPurpose,
  StorageProvider,
  StorageProviderConfigValues,
} from "@/types";

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
  const i18n = useOptionalI18n();
  const [connections, setConnections] = useState<StorageConnection[]>([]);
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | "create" | null>(null);
  const [name, setName] = useState("");
  const [providerId, setProviderId] = useState("s3");
  const [purpose, setPurpose] = useState<StorageConnectionPurpose>("both");
  const [values, setValues] = useState<StorageProviderConfigValues>({
    root: "PrintStash",
    region: "auto",
    addressing_style: "auto",
  });
  const [editing, setEditing] = useState<StorageConnection | null>(null);
  const [removeTarget, setRemoveTarget] = useState<StorageConnection | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.allSettled([listStorageConnections(), getStorageProviders()])
      .then(([rows, catalogue]) => {
        if (active) {
          if (rows.status === "fulfilled") setConnections(rows.value);
          else toast.error(rows.reason);
          if (catalogue.status === "fulfilled") setProviders(catalogue.value);
          else setCatalogueFailed(true);
        }
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

  const selected = providers.find((provider) => provider.id === providerId);
  const use = purpose === "backup" ? "backup" : "library";
  const storedSecrets = editing?.secret_fields_set ?? [];
  function availability(id: string) {
    const uses = providers.find((provider) => provider.id === id)?.uses;
    if (!uses) return undefined;
    if (purpose === "both") return !uses.library.available ? uses.library : uses.backup;
    return uses[purpose];
  }
  const selectedAvailability = availability(providerId);
  function canCreateConnection() {
    return Boolean(
      name.trim() && selected && !providerFormError(selected, values, use, storedSecrets),
    );
  }
  function changeValue(field: string, value: string | number) {
    setValues((current) => {
      const next = { ...current, [field]: value };
      if (
        editing &&
        value === "" &&
        selected &&
        providerFields(selected, use).some((entry) => entry.name === field && entry.secret)
      )
        delete next[field];
      return next;
    });
  }
  function edit(connection: StorageConnection) {
    setEditing(connection);
    setName(connection.name);
    setPurpose(connection.purpose);
    setProviderId(String(connection.configuration.provider ?? connection.kind));
    const editableValues: StorageProviderConfigValues = {};
    for (const [field, value] of Object.entries(connection.configuration)) {
      if (value !== null) editableValues[field] = String(value);
    }
    setValues(editableValues);
  }
  function resetForm() {
    setEditing(null);
    setName("");
    setValues(selected ? providerDefaults(selected, use) : {});
  }
  async function addConnection() {
    if (!canCreateConnection() || !selected) return;
    const transport = selected.transport ?? selected.id;
    if (!isRemoteKind(transport)) return;
    setBusy("create");
    try {
      const body: StorageConnectionCreate = {
        name: name.trim(),
        kind: transport,
        purpose,
        ...splitProviderValues(selected, values, use),
      };
      const created = editing
        ? await updateStorageConnection(editing.id, {
            name: body.name,
            purpose,
            configuration: body.configuration,
            secrets: body.secrets,
          })
        : await createStorageConnection(body);
      setConnections((current) =>
        editing
          ? current.map((row) => (row.id === created.id ? created : row))
          : [...current, created],
      );
      resetForm();
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
                    {connection.uses?.[connection.purpose === "library" ? "library" : "backup"]
                      ?.available === false && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        {storageOperationMessage(
                          connection.uses[connection.purpose === "library" ? "library" : "backup"]
                            .reason,
                          i18n?.t,
                        )}
                      </p>
                    )}
                    {connection.purpose !== "backup" && connection.source_operations && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {storageOperationMessage(
                          connection.source_operations.catalog_purge.reason,
                          i18n?.t,
                        )}
                      </p>
                    )}
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
                        disabled={disabled || busy !== null || catalogueFailed}
                        onClick={() => edit(connection)}
                      >
                        Edit
                      </Button>
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
            <h3 className="text-sm font-semibold text-foreground">
              {editing ? `Edit ${editing.name}` : "Add remote connection"}
            </h3>
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
                value={providerId}
                disabled={disabled || editing !== null}
                onChange={(event) => {
                  const provider = providers.find((entry) => entry.id === event.target.value);
                  if (provider) {
                    setProviderId(provider.id);
                    setValues(providerDefaults(provider, use));
                  }
                }}
              >
                {providers
                  .filter((provider) => provider.id !== "local")
                  .map((provider) => (
                    <option
                      key={provider.id}
                      value={provider.id}
                      disabled={availability(provider.id)?.available === false}
                    >
                      {provider.label}
                    </option>
                  ))}
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
          </div>
          {selected && (
            <StorageProviderFields
              provider={selected}
              values={values}
              onChange={changeValue}
              use={use}
              disabled={disabled || busy !== null}
              storedSecrets={storedSecrets}
              editing={editing !== null}
              onClear={(field) => setValues((current) => ({ ...current, [field]: "" }))}
            />
          )}
          {selected && (
            <ul className="space-y-1 text-xs text-muted-foreground">
              {selected.consequences.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          )}
          {editing && (
            <p className="text-xs text-muted-foreground">
              Leave stored credentials blank to keep them. Target changes are blocked while Library
              sources or backups depend on this connection.
            </p>
          )}
          {purpose === "both" && (
            <p className="rounded-md bg-muted p-3 text-xs leading-relaxed text-muted-foreground">
              Shared connections keep one base folder. Library source paths must stay separate from
              the reserved printstash-backups folder.
            </p>
          )}
          <div className="flex justify-end gap-2">
            {editing && (
              <Button type="button" variant="outline" disabled={busy !== null} onClick={resetForm}>
                Cancel editing
              </Button>
            )}
            {catalogueFailed && (
              <p className="mr-auto text-xs text-muted-foreground">
                {i18n?.t("storage.operationUnavailable") ??
                  "Storage information is unavailable. Reload to try again."}
              </p>
            )}
            {selectedAvailability?.available === false && (
              <p className="mr-auto max-w-xl text-xs text-muted-foreground">
                {storageOperationMessage(selectedAvailability.reason, i18n?.t)}
              </p>
            )}
            <Button
              type="button"
              disabled={
                disabled ||
                loading ||
                catalogueFailed ||
                busy !== null ||
                selectedAvailability?.available === false ||
                !canCreateConnection()
              }
              onClick={() => void addConnection()}
            >
              {busy === "create" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Plus className="h-4 w-4" aria-hidden />
              )}
              {editing ? "Save changes" : "Save connection"}
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
