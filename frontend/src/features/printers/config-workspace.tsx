import { useId, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import { useUiLocale } from "@/lib/i18n";
import { useContentReveal } from "@/lib/use-content-reveal";
import { translate, uiText, type Locale, type MessageKey } from "@/lib/locale";
import type { MoonrakerConfigRead, ProviderJsonObject, ProviderJsonValue } from "@/types";
import { PrinterConfigEditor } from "./config-editor";
import "./config-editor.css";

const tabClassName =
  "shrink-0 px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring transition-colors duration-press motion-reduce:transition-none";
const activeTabClassName = "bg-accent text-accent-foreground";

type ConfigView = "files" | "reported";
type ConfigSource = "klipper_config" | "moonraker_config" | "server_info" | "printer_info";

interface ConfigSection {
  id: string;
  name: string;
  value: ProviderJsonValue;
}

interface ConfigGroup {
  label: MessageKey | null;
  sections: ConfigSection[];
}

function isConfigObject(value: ProviderJsonValue): value is ProviderJsonObject {
  return value instanceof Object && !Array.isArray(value);
}

function sectionGroup(label: MessageKey | null, data: ProviderJsonObject): ConfigGroup {
  return {
    label,
    sections: Object.keys(data)
      .sort((a, b) => a.localeCompare(b))
      .map((name) => ({ id: JSON.stringify([label, name]), name, value: data[name] })),
  };
}

function configGroups(
  source: ConfigSource,
  data: ProviderJsonObject,
  locale: Locale,
): ConfigGroup[] {
  if (source === "klipper_config") {
    const { configfile, ...other } = data;
    if (isConfigObject(configfile)) {
      const { config, settings, ...status } = configfile;
      // Sections live inside configfile.config/settings, not directly under status.
      return [
        sectionGroup("Configured values", isConfigObject(config) ? config : {}),
        sectionGroup("Effective settings", isConfigObject(settings) ? settings : {}),
        sectionGroup("Reported status", { ...other, ...status }),
      ];
    }
  }
  if (source === "moonraker_config") {
    const { config, ...other } = data;
    if (isConfigObject(config)) {
      return [sectionGroup("Configured values", config), sectionGroup("Reported status", other)];
    }
  }
  if (source === "server_info" || source === "printer_info") {
    const name = translate(locale, "Reported values");
    return Object.keys(data).length
      ? [{ label: null, sections: [{ id: source, name, value: data }] }]
      : [];
  }
  return [sectionGroup(null, data)];
}

function ConfigValue({ value }: { value: ProviderJsonValue }) {
  if (value === undefined) return <span>{uiText("Unavailable")}</span>;
  if (!(value instanceof Object)) {
    return (
      <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{String(value) || '""'}</span>
    );
  }

  if (Array.isArray(value)) {
    return value.length ? (
      <ol className="list-decimal space-y-1 ps-5">
        {value.map((item, index) => (
          <li key={index}>
            <ConfigValue value={item} />
          </li>
        ))}
      </ol>
    ) : (
      <span>[]</span>
    );
  }

  const rows = Object.entries(value);
  return rows.length ? (
    <dl className="divide-y divide-border">
      {rows.map(([key, item]) => (
        <div key={key} className="printer-config-property grid gap-1 py-2 first:pt-0 last:pb-0">
          <dt className="text-muted-foreground [overflow-wrap:anywhere]">{key}</dt>
          <dd className="min-w-0 text-foreground">
            <ConfigValue value={item} />
          </dd>
        </div>
      ))}
    </dl>
  ) : (
    <span>{"{}"}</span>
  );
}

function ConfigSections({ groups }: { groups: ConfigGroup[] }) {
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<string | null>(null);
  const term = query.trim().toLocaleLowerCase();
  const filtered = groups
    .map(({ label, sections }) => ({
      label,
      sections: sections.filter(({ name }) => name.toLocaleLowerCase().includes(term)),
    }))
    .filter(({ sections }) => sections.length > 0);
  const sections = filtered.flatMap((group) => group.sections);
  const selected = sections.find(({ id }) => id === selection) ?? sections[0];
  const headingId = useId();
  const content = useContentReveal(selected?.id ?? "", { timing: "press", scroll: "self" });

  return (
    <div className="printer-config-workspace">
      <nav
        aria-label={uiText("Configuration sections")}
        className="printer-config-files space-y-3 bg-muted/25 p-3"
      >
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label={uiText("Search configuration sections")}
          placeholder={uiText("Search sections")}
          className="h-8"
        />
        {filtered.map(({ label, sections: matches }) => (
          <div key={label}>
            {label && (
              <p className="px-2 py-1 text-xs font-medium text-muted-foreground">{uiText(label)}</p>
            )}
            <ul className="space-y-0.5">
              {matches.map((section) => (
                <li key={section.id}>
                  <button
                    type="button"
                    aria-current={section.id === selected?.id ? "true" : undefined}
                    onClick={() => setSelection(section.id)}
                    className="w-full px-2 py-1.5 text-start font-mono text-xs transition-colors duration-press [overflow-wrap:anywhere] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring aria-[current=true]:bg-accent aria-[current=true]:text-accent-foreground motion-reduce:transition-none"
                  >
                    {section.name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {!selected && (
          <p role="status" className="px-2 text-xs text-muted-foreground">
            {term ? uiText("No matching sections.") : uiText("No sections reported.")}
          </p>
        )}
      </nav>
      {selected && (
        <section aria-labelledby={headingId} className="min-w-0">
          <h3
            id={headingId}
            className="border-b px-4 py-3 font-mono text-sm font-semibold [overflow-wrap:anywhere]"
          >
            {selected.name}
          </h3>
          <div ref={content} className="printer-config-values px-4 py-3 font-mono text-sm">
            <ConfigValue value={selected.value} />
          </div>
        </section>
      )}
    </div>
  );
}

function ReportedConfiguration({
  data,
  error,
  loading,
  onRefresh,
  onCopy,
}: {
  data: MoonrakerConfigRead | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
  onCopy: (data: ProviderJsonObject) => void;
}) {
  const [source, setSource] = useState<ConfigSource>("klipper_config");
  const locale = useUiLocale();
  const groups = useMemo(
    () => (data ? configGroups(source, data[source], locale) : []),
    [data, source, locale],
  );

  return (
    <Card className="overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b p-3">
        <TabBar
          tabs={[
            { key: "klipper_config", label: uiText("Klipper config") },
            { key: "moonraker_config", label: uiText("Moonraker config") },
            { key: "server_info", label: uiText("Server info") },
            { key: "printer_info", label: uiText("Printer info") },
          ]}
          active={source}
          onChange={setSource}
          className="max-w-full gap-0.5 overflow-x-auto border bg-muted/30 p-0.5"
          tabClassName={tabClassName}
          activeTabClassName={activeTabClassName}
          showIndicator={false}
        />
        <Button
          variant="outline"
          size="xs"
          disabled={!data}
          onClick={() => data && onCopy(data[source])}
        >
          {uiText("Copy JSON")}
        </Button>
      </header>
      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 border-b px-4 py-3 text-sm text-destructive"
        >
          <span>{error}</span>
          <Button variant="outline" size="xs" disabled={loading} onClick={onRefresh}>
            {uiText("Retry")}
          </Button>
        </div>
      )}
      {data ? (
        <ConfigSections key={source} groups={groups} />
      ) : loading ? (
        <div role="status" className="p-4">
          <span className="sr-only">{uiText("Loading config…")}</span>
          <Skeleton className="h-24" />
        </div>
      ) : null}
      <p className="border-t px-4 py-2 text-xs text-muted-foreground">
        {uiText("Read-only values reported by Moonraker. They do not verify G-code compatibility.")}
      </p>
    </Card>
  );
}

export function PrinterConfigWorkspace({
  printerId,
  ...reported
}: { printerId: number } & Parameters<typeof ReportedConfiguration>[0]) {
  const [view, setView] = useState<ConfigView>("files");

  return (
    <div className="space-y-3">
      <TabBar
        tabs={[
          { key: "files", label: uiText("Configuration files") },
          { key: "reported", label: uiText("Reported configuration") },
        ]}
        active={view}
        onChange={setView}
        className="w-fit max-w-full gap-0.5 overflow-x-auto border bg-muted/30 p-0.5"
        tabClassName={tabClassName}
        activeTabClassName={activeTabClassName}
        showIndicator={false}
      />
      <div hidden={view !== "files"} role="tabpanel" aria-label={uiText("Configuration files")}>
        <PrinterConfigEditor printerId={printerId} />
      </div>
      <div
        hidden={view !== "reported"}
        role="tabpanel"
        aria-label={uiText("Reported configuration")}
      >
        <ReportedConfiguration {...reported} />
      </div>
    </div>
  );
}
