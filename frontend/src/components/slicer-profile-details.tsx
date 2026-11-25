import { useMemo, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@/lib/link";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
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

function SettingsTable({
  settings,
  contained = false,
}: {
  settings: Record<string, string>;
  contained?: boolean;
}) {
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
        <dl
          className={`divide-y divide-border ${contained ? "max-h-80 overflow-y-auto pr-2" : ""}`}
        >
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

type SourceFileCapture = SettingsVariant["sources"][number];

function SourceFile({ file, locale }: { file: SourceFileCapture; locale: string }) {
  const [open, setOpen] = useState(false);
  const hasProcess = Object.keys(file.settings.process).length > 0;
  return (
    <li className="min-w-0 rounded-lg border border-border bg-muted/30">
      <div className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="min-w-0">
          <Link
            className="break-words text-sm font-medium hover:underline"
            href={`/models/${file.model_id}`}
          >
            {file.model_name}
          </Link>
          <p className="break-all text-xs text-muted-foreground">{file.filename}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {new Date(file.uploaded_at).toLocaleString(locale)}
          </p>
        </div>
        {hasProcess && (
          <Button variant="outline" size="xs" aria-expanded={open} onClick={() => setOpen(!open)}>
            {uiText("profiles.fileProcess")}
            <ChevronDown
              aria-hidden
              className={`h-3.5 w-3.5 transition-transform duration-press ${open ? "rotate-180" : ""}`}
            />
          </Button>
        )}
      </div>
      {open && (
        <div className="border-t border-border p-3">
          <SettingsTable settings={file.settings.process} contained />
        </div>
      )}
    </li>
  );
}

function Variant({
  variant,
  index,
  profileName,
}: {
  variant: SettingsVariant;
  index: number;
  profileName: string;
}) {
  const { locale } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const source = variant.sources[0];
  const preset = variant.settings.filament_settings_id ?? variant.settings.printer_settings_id;
  return (
    <article className="min-w-0 py-4">
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
      <Modal
        open={expanded}
        onClose={() => setExpanded(false)}
        title={uiText("Captured setting variant")}
        description={uiText("These values describe the saved files and are read-only.")}
        className="max-w-3xl"
      >
        <div className="space-y-3">
          <dl className="grid gap-3 rounded-lg border border-border bg-muted/30 p-4 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-muted-foreground">{uiText("Profile")}</dt>
              <dd className="font-medium">{profileName}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">{uiText("Variant")}</dt>
              <dd className="font-medium">
                {index + 1}
                {preset ? ` · ${preset}` : ""}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">{uiText("Slicer")}</dt>
              <dd className="font-medium">
                {[source.slicer_name, source.slicer_version].filter(Boolean).join(" ") || "—"}
              </dd>
            </div>
          </dl>
          <SettingsTable settings={variant.settings} />
          <p className="text-xs text-muted-foreground">{uiText("profiles.valuesHelp")}</p>
          <h5 className="pt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {uiText("profiles.sourceFiles")}
          </h5>
          <ul className="space-y-2">
            {variant.sources.map((file) => (
              <SourceFile key={file.file_id} file={file} locale={locale} />
            ))}
          </ul>
        </div>
      </Modal>
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
  const [compareOpen, setCompareOpen] = useState(false);
  const query = useInfiniteQuery(
    profileSettingsOptions(kind, profile.id, profile.edit_epoch, profile.edit_version),
  );
  const denied = query.error instanceof ApiError && [401, 403, 404].includes(query.error.status);
  const captures = useMemo(
    () => (denied ? [] : (query.data?.pages.flatMap((page) => page.items) ?? [])),
    [denied, query.data],
  );
  const variants = useMemo(() => groupProfileSettings(kind, captures), [kind, captures]);
  const first = variants[0]?.settings ?? {};
  const second = variants[1]?.settings ?? {};
  const differences = [...new Set([...Object.keys(first), ...Object.keys(second)])]
    .filter((key) => first[key] !== second[key])
    .sort();
  return (
    <div className="space-y-4 px-4 pb-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-prose text-xs text-muted-foreground">
          {uiText("profiles.integratedHelp")}
        </p>
        <div className="flex flex-wrap gap-2">
          {variants.length > 1 && (
            <Button variant="outline" size="xs" onClick={() => setCompareOpen(true)}>
              {uiText("Compare variants")}
            </Button>
          )}
          <Button
            variant="outline"
            size="xs"
            onClick={() => void query.refetch()}
            loading={query.isFetching}
          >
            {uiText("Refresh")}
          </Button>
        </div>
      </div>
      <Modal
        open={compareOpen && variants.length > 1}
        onClose={() => setCompareOpen(false)}
        title={uiText("Compare slicer variants")}
        description={uiText("Values captured from the G-code; they are read-only.")}
        className="max-w-3xl"
      >
        <div className="space-y-4">
          <dl className="grid gap-3 rounded-lg border border-border bg-muted/30 p-4 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-muted-foreground">{uiText("Profile")}</dt>
              <dd className="font-medium">{profile.name}</dd>
            </div>
            {[variants[0], variants[1]].map((variant, index) => (
              <div key={variant?.key ?? index}>
                <dt className="text-xs text-muted-foreground">
                  {uiText(index === 0 ? "Variant 1" : "Variant 2")}
                </dt>
                <dd className="font-medium">
                  {[
                    variant?.settings.filament_settings_id ?? variant?.settings.printer_settings_id,
                    variant?.sources[0]?.slicer_name,
                    variant?.sources[0]?.slicer_version,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "—"}
                </dd>
              </div>
            ))}
          </dl>
          <p className="text-sm text-muted-foreground">
            {uiText("Only settings that differ are shown.")}
          </p>
          {differences.length === 0 ? (
            <p className="text-sm text-muted-foreground">{uiText("No differences.")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="py-2 pr-4 font-semibold">{uiText("Setting")}</th>
                    <th className="py-2 pr-4 font-semibold">{uiText("Variant 1")}</th>
                    <th className="py-2 font-semibold">{uiText("Variant 2")}</th>
                  </tr>
                </thead>
                <tbody>
                  {differences.map((key) => (
                    <tr key={key} className="border-b border-border">
                      <th className="break-all py-2.5 pr-4 font-mono text-xs font-medium text-muted-foreground">
                        {key}
                      </th>
                      <td className="break-all py-2.5 pr-4">{first[key] ?? "—"}</td>
                      <td className="break-all py-2.5">{second[key] ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Modal>
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
                <div className="divide-y divide-border">
                  {variants.map((variant, index) => (
                    <Variant
                      key={variant.key}
                      variant={variant}
                      index={index}
                      profileName={profile.name}
                    />
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
      className="min-w-0 rounded-lg border border-border"
      aria-label={uiText("profiles.settingsFor", { name: profile.name })}
    >
      <Button
        variant="ghost"
        size="sm"
        className="w-full justify-between px-4 font-semibold"
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
