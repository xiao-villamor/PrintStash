import { CircleAlert, CircleCheck } from "lucide-react";
import { StatePill } from "@/components/state-pill";
import { Button } from "@/components/ui/button";
import { currentLocale, knownUiText, uiText } from "@/lib/locale";
import { providerLabel } from "@/lib/printer-providers";
import type { PrinterDiagnostics, PrinterRead } from "@/types";

const capabilityGroups = [
  {
    titleKey: "Files & dispatch",
    keys: [
      ["can_list_files", "Printer files"],
      ["can_upload", "Upload"],
      ["can_start", "Start"],
    ],
  },
  {
    titleKey: "Print control",
    keys: [
      ["can_pause", "Pause"],
      ["can_resume", "Resume"],
      ["can_cancel", "Cancel"],
      ["can_send_gcode", "G-code commands"],
    ],
  },
  {
    titleKey: "Monitoring",
    keys: [
      ["can_live_status", "Live status"],
      ["can_measure_consumption", "Measured filament consumption"],
    ],
  },
] as const;

function checkLabel(name: string) {
  switch (name) {
    case "configuration":
      return uiText("Connection configuration");
    case "address":
      return uiText("Printer address");
    case "provider_info":
      return uiText("Printer information");
    case "live_status":
      return uiText("Live status");
    default:
      return name;
  }
}

/** What the operator can do about a check failure, when the code names one. */
function checkRemedy(code: string | undefined) {
  switch (code) {
    case "printer_mdns_host_unresolved":
      return uiText(
        "Names ending in .local can't be resolved from the PrintStash container. Enter the printer's IP address instead.",
      );
    case "printer_host_unresolved":
      return uiText(
        "PrintStash can't resolve this name. Check the spelling, or enter the printer's IP address instead.",
      );
    default:
      return null;
  }
}

export function PrinterDiagnosticsPanel({
  printer,
  result,
  checkedAt,
  running,
  error,
  onRun,
  onCopy,
}: {
  printer: PrinterRead;
  result: PrinterDiagnostics | null;
  checkedAt: number;
  running: boolean;
  error: string | null;
  onRun: () => void;
  onCopy?: () => void;
}) {
  const showDetail = result?.checks.some((check) => check.detail?.trim() || check.code?.trim());
  const capabilities = result?.capabilities ?? printer.capabilities;
  const notes = [...new Set([...printer.capabilities.support_notes, ...(result?.notes ?? [])])];
  return (
    <div className="printer-sections-split">
      <section aria-label={uiText("Connection checks")} className="printer-sections-panel">
        <header>
          <h2>{uiText("Connection checks")}</h2>
          <div className="flex flex-wrap gap-2">
            {onCopy && (
              <Button size="xs" variant="outline" onClick={onCopy}>
                {uiText("Copy diagnostics")}
              </Button>
            )}
            <Button size="xs" loading={running} onClick={onRun}>
              {uiText(error ? "Retry" : "Run checks")}
            </Button>
          </div>
        </header>
        <div className="printer-sections-body space-y-2">
          <div
            role="status"
            className="flex flex-wrap items-baseline justify-between gap-2 text-sm"
          >
            <StatePill
              icon={result?.ok && !error ? CircleCheck : error || result ? CircleAlert : undefined}
              destructive={Boolean(error)}
            >
              {running
                ? uiText("Running checks…")
                : error
                  ? uiText("Could not run diagnostics")
                  : result
                    ? uiText(result.ok ? "Provider reachable" : "Needs attention")
                    : uiText("Diagnostics have not loaded yet.")}
            </StatePill>
            {checkedAt > 0 && (
              <time
                className="text-xs text-muted-foreground"
                dateTime={new Date(checkedAt).toISOString()}
              >
                {uiText("Last checked {value1}", {
                  value1: new Date(checkedAt).toLocaleString(currentLocale()),
                })}
              </time>
            )}
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {printer.last_error && printer.status === "offline" && (
            <p className="break-words text-xs text-muted-foreground">{printer.last_error}</p>
          )}
        </div>
        {result?.checks.length ? (
          <div className="overflow-x-auto">
            <table className="printer-check-table" aria-label={uiText("Connection check results")}>
              <thead>
                <tr>
                  <th scope="col">{uiText("Check")}</th>
                  <th scope="col">{uiText("Result")}</th>
                  {showDetail && <th scope="col">{uiText("Detail")}</th>}
                </tr>
              </thead>
              <tbody>
                {result.checks.map((check) => (
                  <tr key={check.name}>
                    <th scope="row">{checkLabel(check.name)}</th>
                    <td>
                      <StatePill
                        icon={check.ok ? CircleCheck : CircleAlert}
                        destructive={!check.ok}
                      >
                        {uiText(check.ok ? "Passed" : "Failed")}
                      </StatePill>
                    </td>
                    {showDetail && (
                      <td>
                        {checkRemedy(check.code) && (
                          <span className="block">{checkRemedy(check.code)}</span>
                        )}
                        {check.detail}
                        {check.code && (
                          <code className="block text-xs text-muted-foreground">{check.code}</code>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          result && (
            <p className="printer-sections-body text-sm text-muted-foreground">
              {uiText("No connection checks were reported.")}
            </p>
          )
        )}
      </section>
      <section aria-label={uiText("Provider capabilities")} className="printer-sections-panel">
        <header>
          <h2>{uiText("Provider capabilities")}</h2>
          <span className="text-xs text-muted-foreground">{providerLabel(printer)}</span>
        </header>
        {capabilityGroups.map(({ titleKey, keys }) => (
          <div key={titleKey} className="printer-sections-body border-b border-border">
            <h3 className="mb-2 text-sm font-medium">{uiText(titleKey)}</h3>
            <dl className="space-y-1 text-xs">
              {keys.map(([key, label]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt>{uiText(label)}</dt>
                  <dd className="text-muted-foreground">
                    {uiText(capabilities[key] ? "Available" : "Unavailable")}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
        {(notes.length > 0 || !!result?.unsupported_actions.length) && (
          <section
            aria-label={uiText("Provider support")}
            className="printer-sections-body space-y-2 text-xs text-muted-foreground"
          >
            <h3 className="text-sm font-medium text-foreground">{uiText("Provider support")}</h3>
            {notes.map((note) => (
              <p key={note}>{knownUiText(note)}</p>
            ))}
            {!!result?.unsupported_actions.length && (
              <p>
                {uiText("Unavailable")} ·{" "}
                {result.unsupported_actions.map((action) => knownUiText(action)).join(" · ")}
              </p>
            )}
          </section>
        )}
      </section>
    </div>
  );
}
