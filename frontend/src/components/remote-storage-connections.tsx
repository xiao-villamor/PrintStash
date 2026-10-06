import { knownUiText } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import {
  storageConnectionsOptions,
  storageProvidersOptions,
  storageReadDenied,
  useStorageConnectionCommand,
} from "@/lib/queries/settings-storage";
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
import { StorageProviderFields } from "@/components/storage-provider-fields";
import {
  providerDefaults,
  providerFields,
  providerFormError,
  splitProviderValues,
} from "@/lib/storage-provider-form";
import type { StorageConnectionCreate } from "@/lib/api/storage-connections";
import { toast } from "@/lib/toast";
import { useI18n } from "@/lib/i18n";
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
  const i18n = useI18n();
  const { user } = useAuth();
  const live = useRef(true);
  const generation = useRef(0);
  const [retired, setRetired] = useState(false);
  const connectionQuery = useQuery({
    ...storageConnectionsOptions(),
    enabled: !!user?.is_superuser && !retired,
  });
  const providerQuery = useQuery({ ...storageProvidersOptions(), enabled: !retired });
  const command = useStorageConnectionCommand();
  const connectionsDenied =
    connectionQuery.isError && storageReadDenied(parseApiError(connectionQuery.error));
  const providersDenied =
    providerQuery.isError && storageReadDenied(parseApiError(providerQuery.error));
  const connections = !user?.is_superuser || connectionsDenied ? [] : (connectionQuery.data ?? []);
  const providers = providersDenied ? [] : (providerQuery.data ?? []);
  const catalogueFailed = providerQuery.isError;
  const connectionsFailed = connectionQuery.isError || !user?.is_superuser;
  const loading = !!user?.is_superuser && (connectionQuery.isPending || providerQuery.isPending);
  const busy =
    command.pending === null
      ? null
      : command.pending.kind === "create"
        ? "create"
        : command.pending.id;
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
    live.current = true;
    const release = onAuthChange(() => {
      generation.current += 1;
      setValues({});
      setName("");
      setEditing(null);
      setRemoveTarget(null);
      setPurpose("library");
      setRetired(true);
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const unavailable =
    disabled ||
    retired ||
    connectionsFailed ||
    catalogueFailed ||
    !connectionQuery.data ||
    !providerQuery.data;
  const editUnavailable = editing !== null && !connections.some((row) => row.id === editing.id);
  function current(session: number) {
    return live.current && !retired && session === getSessionVersion();
  }

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
    generation.current += 1;
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
    generation.current += 1;
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
    generation.current += 1;
    setEditing(null);
    setName("");
    setPurpose("library");
    setValues(selected ? providerDefaults(selected, "library") : {});
  }
  function chooseProvider(provider: StorageProvider) {
    generation.current += 1;
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
    if (unavailable || editUnavailable || busy !== null || !canCreateConnection() || !selected)
      return;
    const transport = selected.transport ?? selected.id;
    if (!isRemoteKind(transport)) return;
    const session = getSessionVersion();
    const captured = generation.current;
    const body: StorageConnectionCreate = {
      name: name.trim(),
      kind: transport,
      purpose,
      ...splitProviderValues(selected, values, use),
    };
    try {
      const receipt = await command.mutateAsync(
        editing
          ? {
              session,
              kind: "update",
              id: editing.id,
              payload: {
                name: body.name,
                purpose: body.purpose,
                configuration: body.configuration,
                secrets: body.secrets,
              },
            }
          : { session, kind: "create", payload: body },
      );
      if (!current(session)) return;
      if (receipt.kind !== "saved") throw new Error("Expected a saved storage connection");
      if (generation.current === captured) resetForm();
      toast.success(uiText("Remote storage connection saved."));
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function probe(connection: StorageConnection) {
    if (unavailable || busy !== null) return;
    const session = getSessionVersion();
    try {
      await command.mutateAsync({ session, kind: "probe", id: connection.id });
      if (current(session))
        toast.success(uiText("{value1} is reachable.", { value1: connection.name }));
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function toggle(connection: StorageConnection) {
    if (unavailable || busy !== null) return;
    const session = getSessionVersion();
    try {
      const receipt = await command.mutateAsync({
        session,
        kind: "update",
        id: connection.id,
        payload: { enabled: !connection.enabled },
      });
      if (!current(session)) return;
      if (receipt.kind !== "saved") throw new Error("Expected a saved storage connection");
      toast.success(
        receipt.connection.enabled
          ? uiText("Remote connection resumed.")
          : uiText("Remote connection paused."),
      );
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function changePurpose(
    connection: StorageConnection,
    nextPurpose: StorageConnectionPurpose,
  ) {
    if (unavailable || busy !== null) return;
    const session = getSessionVersion();
    try {
      const receipt = await command.mutateAsync({
        session,
        kind: "update",
        id: connection.id,
        payload: { purpose: nextPurpose },
      });
      if (!current(session)) return;
      if (receipt.kind !== "saved") throw new Error("Expected a saved storage connection");
      toast.success(
        uiText("{value1} will serve {value2}.", {
          value1: receipt.connection.name,
          value2: purposeLabel(receipt.connection.purpose).toLowerCase(),
        }),
      );
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function remove(connection: StorageConnection) {
    if (unavailable || busy !== null) return;
    const session = getSessionVersion();
    try {
      await command.mutateAsync({ session, kind: "delete", id: connection.id });
      if (!current(session)) return;
      setRemoveTarget((target) => (target?.id === connection.id ? null : target));
      toast.success(uiText("Remote storage connection removed."));
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  if (retired) return null;

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

        {connectionsFailed && connections.length > 0 && (
          <div role="alert" className="flex items-center gap-2 px-4 py-3 text-sm sm:px-5">
            <p>{i18n.t("storage.connectionsLoadFailed")}</p>
            <Button
              variant="outline"
              size="sm"
              disabled={!user?.is_superuser}
              onClick={() => void connectionQuery.refetch()}
            >
              {uiText("Retry")}
            </Button>
          </div>
        )}
        {catalogueFailed && (
          <div role="alert" className="flex items-center gap-2 px-4 py-3 text-sm sm:px-5">
            <p>{i18n.t("storage.providersLoadFailed")}</p>
            <Button variant="outline" size="sm" onClick={() => void providerQuery.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        {editUnavailable && !connectionsFailed && (
          <p role="alert" className="px-4 py-3 text-sm sm:px-5">
            {i18n.t("storage.connectionUnavailable")}
          </p>
        )}
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
          ) : connectionsFailed && connections.length === 0 ? (
            <div role="alert" className="flex items-center gap-2 px-4 pb-4 text-sm sm:px-5">
              <p>{i18n.t("storage.connectionsLoadFailed")}</p>
              <Button
                variant="outline"
                size="sm"
                disabled={!user?.is_superuser}
                onClick={() => void connectionQuery.refetch()}
              >
                {uiText("Retry")}
              </Button>
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
                        disabled={unavailable || busy !== null}
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
                        disabled={unavailable || busy !== null}
                        onClick={() => edit(connection)}
                      >
                        {uiText("Edit")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={unavailable || busy !== null || !connection.enabled}
                        onClick={() => void probe(connection)}
                      >
                        <CheckCircle2 className="h-4 w-4" aria-hidden />
                        {uiText(" Test")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={unavailable || busy !== null}
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
                        disabled={unavailable || busy !== null}
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
        ) : connectionsDenied || !user?.is_superuser ? null : (
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
                        unavailable ||
                        editUnavailable ||
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
                  const providerUnavailable = providerAvailability?.available === false;
                  const reason = providerUnavailable ? providerAvailability.reason : undefined;
                  return (
                    <Button
                      key={provider.id}
                      type="button"
                      variant="outline"
                      aria-label={knownUiText(provider.label)}
                      aria-pressed={provider.id === providerId}
                      disabled={
                        unavailable || editUnavailable || editing !== null || providerUnavailable
                      }
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
                  disabled={unavailable || editUnavailable}
                  placeholder={uiText("Workshop storage")}
                  onChange={(event) => {
                    generation.current += 1;
                    setName(event.target.value);
                  }}
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
                      disabled={unavailable || editUnavailable}
                      onClick={() => {
                        generation.current += 1;
                        setPurpose(value);
                      }}
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
                  disabled={unavailable || editUnavailable || busy !== null}
                  storedSecrets={storedSecrets}
                  editing={editing !== null}
                  onClear={(field) => {
                    generation.current += 1;
                    setValues((current) => ({ ...current, [field]: "" }));
                  }}
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
                  unavailable ||
                  editUnavailable ||
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

        {!connectionsDenied && user?.is_superuser && (
          <ConfirmModal
            open={removeTarget !== null && !unavailable}
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
        )}
      </section>
    </Localized>
  );
}
