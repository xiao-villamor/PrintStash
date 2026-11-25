import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import type * as React from "react";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";

// Consistent section shell used across every settings section: the heading and
// its actions sit above a light bordered box that holds only rows (the Linear
// and Cursor settings pattern). A `danger` card is the Danger zone.
export function SettingsCard({
  title,
  description,
  action,
  footer,
  children,
  className,
  stackActionOnMobile = false,
  hideHeader = false,
  tone = "default",
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  stackActionOnMobile?: boolean;
  hideHeader?: boolean;
  tone?: "default" | "danger";
}) {
  useUiLocale();
  return (
    <section role="group" aria-label={title} className={cn("settings-card", className)}>
      {!hideHeader && (
        <SettingsGroupHead
          title={title}
          description={description}
          action={action}
          stackActionOnMobile={stackActionOnMobile}
        />
      )}
      <div className="settings-card-box" data-tone={tone}>
        {children}
        {footer && <div className="settings-card-foot">{footer}</div>}
      </div>
    </section>
  );
}

/** Binary setting: the shared Switch with its state words localized. */
export function SettingsSwitch({
  checked,
  onChange,
  label,
  descriptionId,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  descriptionId?: string;
  disabled?: boolean;
}) {
  return (
    <Switch
      className="settings-row-switch"
      checked={checked}
      onChange={onChange}
      label={label}
      descriptionId={descriptionId}
      disabled={disabled}
      stateLabels={{ on: uiText("On"), off: uiText("Off") }}
    />
  );
}

/**
 * The one inline notice for Settings: a neutral bordered panel whose tone is
 * carried only by a small icon (and red text for errors), never a tinted fill.
 */
export function SettingsNotice({
  tone,
  title,
  children,
  action,
  role,
  className,
}: {
  tone: "info" | "success" | "warning" | "danger";
  title?: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
  role?: "alert" | "status";
  className?: string;
}) {
  const Icon =
    tone === "success"
      ? CheckCircle2
      : tone === "warning"
        ? AlertTriangle
        : tone === "danger"
          ? AlertCircle
          : Info;
  return (
    <div
      role={role}
      className={cn("flex items-start gap-2.5 border border-border bg-card p-3", className)}
    >
      <Icon
        aria-hidden
        className={cn(
          "mt-0.5 h-4 w-4 shrink-0",
          tone === "warning"
            ? "text-warning"
            : tone === "danger"
              ? "text-destructive"
              : "text-muted-foreground",
        )}
      />
      <div className="min-w-0 flex-1 space-y-1 text-xs leading-relaxed text-muted-foreground">
        {title && (
          <p
            className={cn(
              "text-[13px] font-medium",
              tone === "danger" ? "text-destructive" : "text-foreground",
            )}
          >
            {title}
          </p>
        )}
        {children}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/** Aligned status text: monochrome, a warning dot or red text for failures. */
export function SettingsStatus({
  tone,
  children,
}: {
  tone?: "warning" | "danger";
  children: React.ReactNode;
}) {
  return (
    <span className="settings-status" data-tone={tone}>
      {children}
    </span>
  );
}

/**
 * The one group heading in Settings: the title, a description at reading width
 * and the group's actions on the right. It sits above its box, never inside it.
 * Panels that are not a SettingsCard use this directly instead of rebuilding it.
 */
export function SettingsGroupHead({
  title,
  description,
  action,
  id,
  level = 3,
  stackActionOnMobile = false,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  /** Id of the heading, for regions labelled by it. */
  id?: string;
  level?: 2 | 3;
  stackActionOnMobile?: boolean;
  className?: string;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div
      className={cn(
        "settings-card-head mb-2.5 flex items-end justify-between gap-3",
        stackActionOnMobile && "flex-col sm:flex-row sm:items-end",
        className,
      )}
    >
      <div className="min-w-0">
        <Heading id={id} className="text-sm font-semibold text-foreground">
          {title}
        </Heading>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action && (
        <div
          className={cn(
            "flex flex-shrink-0 flex-wrap items-center gap-2",
            stackActionOnMobile && "w-full sm:w-auto",
          )}
        >
          {action}
        </div>
      )}
    </div>
  );
}

/** One compact line for a list with nothing in it: text left, optional action right. */
export function SettingsEmptyRow({
  children,
  action,
  className,
}: {
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-4 py-3.5 text-sm text-muted-foreground",
        className,
      )}
    >
      <span className="min-w-0">{children}</span>
      {action && <span className="shrink-0">{action}</span>}
    </div>
  );
}

/** Where a setting applies: only this browser, or the whole Vault. */
export function SettingsScope({ scope }: { scope: "browser" | "vault" }) {
  useUiLocale();
  return (
    <span className="settings-scope" data-scope={scope}>
      {scope === "browser" ? uiText("This browser") : uiText("Whole Vault")}
    </span>
  );
}
