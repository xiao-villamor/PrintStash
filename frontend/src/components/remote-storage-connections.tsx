import { knownUiText } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Cloud,
  Loader2,
  Network,
  PauseCircle,
  PlayCircle,
  Plus,
  Server,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input, inputClasses } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { Skeleton } from "@/components/ui/skeleton";
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
  ProviderCategory,
  StorageConnection,
  StorageConnectionPurpose,
  StorageProvider,
  StorageProviderConfigValues,
} from "@/types";

type RemoteKind = Exclude<LibrarySourceKind, "mounted">;

const REMOTE_KINDS: readonly RemoteKind[] = ["s3", "webdav", "sftp", "gdrive"];
const PURPOSES: readonly StorageConnectionPurpose[] = ["library", "backup", "both"];
const REMOTE_CATEGORIES = [
  {
    id: "s3_compatible",
    get label() {
      return uiText("S3-compatible object storage");
    },
    icon: Cloud,
  },
  {
    id: "nextcloud_webdav",
    get label() {
      return uiText("Nextcloud and WebDAV");
    },
    icon: Network,
  },
  {
    id: "nas_sftp",
    get label() {
      return uiText("NAS over SFTP");
    },
    icon: Server,
  },
  {
    id: "consumer_cloud",
    get label() {
      return uiText("Google Drive");
    },
    icon: Cloud,
  },
] as const satisfies ReadonlyArray<{
  id: ProviderCategory;
  label: string;
  icon: typeof Cloud;
}>;
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
  if (purpose === "library") return uiText("Library sources");
  if (purpose === "backup") return uiText("Backup replicas");
  return uiText("Backups + libraries");
}

export function RemoteStorageConnections({ disabled = false }: { disabled?: boolean }) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const [connections, setConnections] = useState<StorageConnection[]>([]);
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | "create" | null>(null);
  const [name, setName] = useState("");
  const [providerId, setProviderId] = useState("s3");
  const [category, setCategory] = useState<ProviderCategory>("s3_compatible");
  const [purpose, setPurpose] = useState<StorageConnectionPurpose>("library");
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
  const remoteProviders = providers.filter((provider) =>
    isRemoteKind(provider.transport ?? provider.id),
  );
  const categoryProviders = remoteProviders.filter((provider) => provider.category === category);
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
    const nextProviderId = String(connection.configuration.provider ?? connection.kind);
    setProviderId(nextProviderId);
    setCategory(providers.find((provider) => provider.id === nextProviderId)?.category ?? category);
    const editableValues: StorageProviderConfigValues = {};
    for (const [field, value] of Object.entries(connection.configuration)) {
      if (value !== null) editableValues[field] = String(value);
    }
    setValues(editableValues);
  }
  function resetForm() {
    setEditing(null);
    setName("");
    setPurpose("library");
    setValues(selected ? providerDefaults(selected, "library") : {});
  }
  function chooseProvider(provider: StorageProvider) {
    setProviderId(provider.id);
    setCategory(provider.category);
    setValues(providerDefaults(provider, use));
  }
  function chooseCategory(nextCategory: ProviderCategory) {
    const choices = remoteProviders.filter((provider) => provider.category === nextCategory);
    // selectable describes managed Vault storage; remote profiles use per-purpose availability.
    const firstAvailable = choices.find(
      (provider) => availability(provider.id)?.available !== false,
    );
    const first = firstAvailable ?? choices[0];
    if (first) chooseProvider(first);
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
      toast.success(uiText("Remote storage connection saved."));
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
      toast.success(uiText("{value1} is reachable.", { value1: String(connection.name) }));
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
      toast.success(
        updated.enabled
          ? uiText("Remote connection resumed.")
          : uiText("Remote connection paused."),
      );
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
      toast.success(
        uiText("{value1} will serve {value2}.", {
          value1: String(connection.name),
          value2: String(purposeLabel(nextPurpose).toLowerCase()),
        }),
      );
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
      toast.success(uiText("Remote storage connection removed."));
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Localized>
      <section
        role="region"
        aria-label={uiText("Remote storage")}
        className="overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-sm"
      >
        <header className="flex items-start gap-3 border-b border-border px-4 py-4 sm:px-5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Cloud className="h-4 w-4" aria-hidden />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{uiText("Remote storage")}</h2>
            <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted-foreground">
              {uiText(
                "Connect a remote location once, then use it for off-site backup replicas, read-only Library sources, or both. Credentials remain encrypted on this server.",
              )}
            </p>
          </div>
        </header>

        <div className="border-b border-border">
          <div className="px-4 py-3 sm:px-5">
            <h3 className="text-sm font-semibold text-foreground">{uiText("Connections")}</h3>
            {connections.length > 0 && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {uiText(
                  "Pausing a connection stops new backup copies and library scans without forgetting its credentials.",
                )}
              </p>
            )}
          </div>
          {loading ? (
            <div
              role="status"
              aria-label={uiText("Loading connections…")}
              className="space-y-3 px-4 pb-4 sm:px-5"
            >
              <Skeleton className="h-14 w-full" />
              <span className="sr-only">{uiText("Loading connections…")}</span>
            </div>
          ) : connections.length === 0 ? (
            <p className="mx-4 mb-4 rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground sm:mx-5">
              {uiText("No remote storage connected yet.")}
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
                      {connection.kind === "gdrive" && (
                        <Badge variant="secondary">{uiText("Beta")}</Badge>
                      )}
                      <Badge variant={connection.enabled ? "secondary" : "outline"}>
                        {connection.enabled ? uiText("Enabled") : uiText("Paused")}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {uiText("counts.credentials", { count: connection.secret_fields_set.length })}
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
                      {uiText("Use for")}
                      <select
                        className={SELECT}
                        aria-label={uiText("Use {value1} for", { value1: String(connection.name) })}
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
                        {uiText("Edit")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={disabled || busy !== null || !connection.enabled}
                        onClick={() => void probe(connection)}
                      >
                        <CheckCircle2 className="h-4 w-4" aria-hidden />
                        {uiText(" Test")}
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
                        {connection.enabled ? uiText("Pause") : uiText("Resume")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={disabled || busy !== null}
                        onClick={() => setRemoveTarget(connection)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                        {uiText(" Remove")}
                      </Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {loading ? (
          <div
            role="status"
            aria-label={uiText("Add remote connection")}
            className="space-y-5 px-4 py-4 sm:px-5 sm:py-5"
          >
            <Skeleton className="h-5 w-44" />
            <div className="grid gap-2 sm:grid-cols-2">
              {REMOTE_CATEGORIES.map((choice) => (
                <Skeleton key={choice.id} className="h-12 w-full" />
              ))}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {Array.from({ length: 8 }, (_, item) => (
                <Skeleton key={item} className="h-12 w-full" />
              ))}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
            <span className="sr-only">{uiText("Loading connections…")}</span>
          </div>
        ) : (
          <div className="space-y-5 px-4 py-4 sm:px-5 sm:py-5">
            <div>
              <h3 className="text-sm font-semibold text-foreground">
                {editing
                  ? uiText("Edit {value1}", { value1: String(editing.name) })
                  : uiText("Add remote connection")}
              </h3>
            </div>
            <fieldset className="space-y-2">
              <legend className="text-xs font-mono uppercase tracking-wider text-on-surface-variant">
                {uiText("Storage category")}
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {REMOTE_CATEGORIES.map((choice) => {
                  const Icon = choice.icon;
                  return (
                    <Button
                      key={choice.id}
                      type="button"
                      variant="outline"
                      aria-pressed={category === choice.id}
                      disabled={
                        disabled ||
                        editing !== null ||
                        !remoteProviders.some((provider) => provider.category === choice.id)
                      }
                      onClick={() => chooseCategory(choice.id)}
                      className={cn(
                        "h-auto min-h-11 justify-start gap-2 whitespace-normal px-3 py-3 text-left",
                        category === choice.id &&
                          "border-transparent bg-accent text-accent-foreground hover:bg-accent",
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" aria-hidden />
                      {choice.label}
                    </Button>
                  );
                })}
              </div>
            </fieldset>
            <fieldset className="space-y-2">
              <legend className="text-xs font-mono uppercase tracking-wider text-on-surface-variant">
                {uiText("Provider")}
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {categoryProviders.map((provider) => {
                  const providerAvailability = availability(provider.id);
                  const unavailable = providerAvailability?.available === false;
                  const reason = unavailable ? providerAvailability.reason : undefined;
                  return (
                    <Button
                      key={provider.id}
                      type="button"
                      variant="outline"
                      aria-label={knownUiText(provider.label)}
                      aria-pressed={provider.id === providerId}
                      disabled={disabled || editing !== null || unavailable}
                      onClick={() => chooseProvider(provider)}
                      className={cn(
                        "h-auto min-h-12 justify-start whitespace-normal px-3 py-3 text-left",
                        provider.id === providerId &&
                          "border-transparent bg-accent text-accent-foreground hover:bg-accent",
                      )}
                    >
                      <span>
                        <span className="block text-sm font-medium">
                          {knownUiText(provider.label)}
                        </span>
                        {reason && (
                          <span className="block text-xs font-normal opacity-70">
                            {storageOperationMessage(reason, i18n?.t)}
                          </span>
                        )}
                      </span>
                    </Button>
                  );
                })}
              </div>
            </fieldset>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className={FIELD_LABEL}>
                {uiText("Connection name")}
                <Input
                  value={name}
                  maxLength={128}
                  disabled={disabled}
                  placeholder={uiText("Workshop storage")}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <fieldset className="space-y-1.5">
                <legend className="text-xs font-medium text-on-surface-variant">
                  {uiText("Use for")}
                </legend>
                <div className="grid grid-cols-3 gap-1">
                  {PURPOSES.map((value) => (
                    <Button
                      key={value}
                      type="button"
                      variant="outline"
                      aria-pressed={purpose === value}
                      disabled={disabled}
                      onClick={() => setPurpose(value)}
                      className={cn(
                        "h-auto min-h-10 whitespace-normal px-2 py-2 text-xs",
                        purpose === value &&
                          "border-transparent bg-accent text-accent-foreground hover:bg-accent",
                      )}
                    >
                      {purposeLabel(value)}
                    </Button>
                  ))}
                </div>
              </fieldset>
            </div>
            {selected && (
              <div className="space-y-4 rounded-lg border border-outline-variant bg-surface-container-low p-4">
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
                {selected.consequences.length > 0 && (
                  <ul className="space-y-1 text-xs text-muted-foreground">
                    {selected.consequences.map((text) => (
                      <li key={text}>{knownUiText(text)}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {editing && (
              <p className="text-xs text-muted-foreground">
                {uiText(
                  "Leave stored credentials blank to keep them. Target changes are blocked while Library sources or backups depend on this connection.",
                )}
              </p>
            )}
            {purpose === "both" && (
              <p className="rounded-md bg-muted p-3 text-xs leading-relaxed text-muted-foreground">
                {uiText(
                  "Shared connections keep one base folder. Library source paths must stay separate from the reserved printstash-backups folder.",
                )}
              </p>
            )}
            <div className="flex justify-end gap-2">
              {editing && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy !== null}
                  onClick={resetForm}
                >
                  {uiText("Cancel editing")}
                </Button>
              )}
              {catalogueFailed && (
                <p className="mr-auto text-xs text-muted-foreground">
                  {i18n?.t("storage.operationUnavailable") ??
                    uiText("Storage information is unavailable. Reload to try again.")}
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
                {editing ? uiText("Save changes") : uiText("Save connection")}
              </Button>
            </div>
          </div>
        )}

        <ConfirmModal
          open={removeTarget !== null}
          onClose={() => setRemoveTarget(null)}
          onConfirm={() => {
            if (removeTarget) void remove(removeTarget);
          }}
          title={uiText("Remove remote connection?")}
          description={
            removeTarget
              ? uiText(
                  "“{value1}” can only be removed when no Library source or owned backup still depends on it.",
                  { value1: String(removeTarget.name) },
                )
              : ""
          }
          confirmLabel={uiText("Remove connection")}
          busy={removeTarget !== null && busy === removeTarget.id}
        />
      </section>
    </Localized>
  );
}
