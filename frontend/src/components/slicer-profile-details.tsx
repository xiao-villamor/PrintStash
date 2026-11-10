import { useMemo, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@/lib/link";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { groupProfileSettings, type SettingsVariant } from "@/lib/profile-settings";
import { profileSettingsOptions } from "@/lib/queries/slicer-profiles";
import type { FilamentProfileRead } from "@/types";
import type { ProfileSettingsKind } from "@/types/slicer-profiles";

type Profile = Pick<FilamentProfileRead, "id" | "name" | "edit_epoch" | "edit_version">;

function SettingsTable({ settings }: { settings: Record<string, string> }) {
  const [search, setSearch] = useState("");
  const rows = Object.entries(settings).filter(([key, value]) =>
    `${key} ${value}`.toLowerCase().includes(search.toLowerCase().trim()),
  );
  return (
    <div className="space-y-3">
      <Input
        aria-label={uiText("profiles.searchSettings")}
        placeholder={uiText("profiles.searchSettings")}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {rows.length === 0 ? (
        <p className="py-3 text-sm text-muted-foreground">{uiText("profiles.noSettings")}</p>
      ) : (
        <dl className="divide-y divide-border">
          {rows.map(([key, value]) => (
            <div key={key} className="grid gap-1 py-2 text-sm sm:grid-cols-2 sm:gap-4">
              <dt className="min-w-0 break-words font-mono text-xs text-muted-foreground">{key}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function Variant({ variant, index }: { variant: SettingsVariant; index: number }) {
  const { locale } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const source = variant.sources[0];
  const preset = variant.settings.filament_settings_id ?? variant.settings.printer_settings_id;
  return (
    <article className="min-w-0 p-3 sm:p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <h4 className="break-words text-sm font-medium">
            {uiText("profiles.variant", { number: index + 1 })}
            {preset ? ` · ${preset}` : ""}
          </h4>
          <p className="break-words text-xs text-muted-foreground">
            {[source.printer_model, source.slicer_name, source.slicer_version]
              .filter(Boolean)
              .join(" · ")}
            {" · "}
            {uiText("profiles.sourceCount", { count: variant.sources.length })}
          </p>
        </div>
        <Button
          variant="outline"
          size="xs"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {uiText(expanded ? "profiles.hideSettings" : "profiles.viewSettings")}
          <ChevronDown
            aria-hidden
            className={`h-3.5 w-3.5 transition-transform duration-press ${expanded ? "rotate-180" : ""}`}
          />
        </Button>
      </div>
      {expanded && (
        <div className="mt-3 space-y-3 border-t pt-3">
          <SettingsTable settings={variant.settings} />
          <p className="text-xs text-muted-foreground">{uiText("profiles.valuesHelp")}</p>
          <h5 className="text-xs font-semibold">{uiText("profiles.sourceFiles")}</h5>
          <ul className="space-y-3">
            {variant.sources.map((file) => (
              <li key={file.file_id} className="min-w-0 space-y-1 rounded-md bg-muted/30 p-3">
                <Link
                  className="break-words text-sm text-primary underline underline-offset-2"
                  href={`/models/${file.model_id}`}
                >
                  {file.filename} · {file.model_name}
                </Link>
                <p className="text-xs text-muted-foreground">
                  {new Date(file.uploaded_at).toLocaleString(locale)}
                </p>
                {Object.keys(file.settings.process).length > 0 && (
                  <details className="text-sm">
                    <summary className="cursor-action py-1">
                      {uiText("profiles.fileProcess")}
                    </summary>
                    <SettingsTable settings={file.settings.process} />
                  </details>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function ProfileSettingsContent({
  kind,
  profile,
}: {
  kind: ProfileSettingsKind;
  profile: Profile;
}) {
  const query = useInfiniteQuery(
    profileSettingsOptions(kind, profile.id, profile.edit_epoch, profile.edit_version),
  );
  const denied = query.error instanceof ApiError && [401, 403, 404].includes(query.error.status);
  const captures = useMemo(
    () => (denied ? [] : (query.data?.pages.flatMap((page) => page.items) ?? [])),
    [denied, query.data],
  );
  const variants = useMemo(() => groupProfileSettings(kind, captures), [kind, captures]);
  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-prose text-xs text-muted-foreground">
          {uiText("profiles.integratedHelp")}
        </p>
        <Button
          variant="outline"
          size="xs"
          onClick={() => void query.refetch()}
          loading={query.isFetching}
        >
          {uiText("Refresh")}
        </Button>
      </div>
      {query.isError && (
        <div role="alert" className="space-y-2 text-sm text-destructive">
          <p>{userMessage(query.error)}</p>
          <Button variant="outline" size="xs" onClick={() => void query.refetch()}>
            {uiText("Retry")}
          </Button>
        </div>
      )}
      {query.isPending ? (
        <Skeleton className="h-16" />
      ) : (
        !denied &&
        query.data && (
          <>
            {variants.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {uiText(
                  query.hasNextPage ? "profiles.noLoadedSettings" : "profiles.noProfileSettings",
                )}
              </p>
            ) : (
              <>
                <p className="text-xs text-muted-foreground">
                  {uiText("profiles.loadedSummary", {
                    variants: variants.length,
                    files: new Set(captures.map((file) => file.file_id)).size,
                  })}
                </p>
                <div className="divide-y divide-border overflow-hidden rounded-md border bg-background">
                  {variants.map((variant, index) => (
                    <Variant key={variant.key} variant={variant} index={index} />
                  ))}
                </div>
              </>
            )}
            {query.hasNextPage && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  {uiText("profiles.moreSourcesHelp")}
                </p>
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => void query.fetchNextPage()}
                  loading={query.isFetchingNextPage}
                  disabled={query.isFetching}
                >
                  {uiText("profiles.loadSources")}
                </Button>
              </div>
            )}
          </>
        )
      )}
    </div>
  );
}

export function ProfileSettingsDetails({
  kind,
  profile,
  initiallyExpanded = false,
}: {
  kind: ProfileSettingsKind;
  profile: Profile;
  initiallyExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  return (
    <section
      className="min-w-0"
      aria-label={uiText("profiles.settingsFor", { name: profile.name })}
    >
      <Button
        variant="ghost"
        size="xs"
        className="-ml-2 font-semibold"
        aria-label={uiText(
          expanded ? "profiles.closeProfileSettings" : "profiles.openProfileSettings",
        )}
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        {uiText("profiles.settingsTitle")}
        <ChevronDown
          aria-hidden
          className={`h-3.5 w-3.5 transition-transform duration-press ${expanded ? "rotate-180" : ""}`}
        />
      </Button>
      {expanded && (
        <ProfileSettingsContent
          key={`${kind}/${profile.id}/${profile.edit_epoch}/${profile.edit_version}`}
          kind={kind}
          profile={profile}
        />
      )}
    </section>
  );
}
