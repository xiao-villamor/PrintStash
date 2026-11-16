import { AlertCircle, AlertTriangle, CheckCircle2, Info, type LucideIcon } from "lucide-react";
import type * as React from "react";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";

// Consistent card shell used across every settings section.
export function SettingsCard({
  icon: Icon,
  title,
  description,
  action,
  footer,
  children,
  className,
  stackActionOnMobile = false,
  hideHeader = false,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  stackActionOnMobile?: boolean;
  hideHeader?: boolean;
}) {
  useUiLocale();
  return (
    <div
      role="group"
      aria-label={title}
      className={cn(
        "settings-card overflow-hidden rounded-lg border border-border bg-card text-card-foreground",
        className,
      )}
    >
      {!hideHeader && (
        <SettingsGroupHead
          icon={Icon}
          title={title}
          description={description}
          action={action}
          stackActionOnMobile={stackActionOnMobile}
        />
      )}
      {children}
      {footer && <div className="settings-card-foot">{footer}</div>}
    </div>
  );
}

/** Binary setting: the shared Switch with its state words localized. */
export function SettingsSwitch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <Switch
      className="settings-row-switch"
      checked={checked}
      onChange={onChange}
      label={label}
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
 * The one group heading in Settings: a 32px icon tile, the title, a
 * description at reading width and the group's actions on the right. Panels
 * that are not a SettingsCard use this directly instead of rebuilding it.
 */
export function SettingsGroupHead({
  icon: Icon,
  title,
  description,
  action,
  id,
  level = 3,
  stackActionOnMobile = false,
  className,
}: {
  icon?: LucideIcon;
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
        "settings-card-head flex items-start justify-between gap-3 border-b border-border px-4 py-4 sm:px-5",
        stackActionOnMobile && "flex-col sm:flex-row",
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-3">
        {Icon && (
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center bg-muted text-foreground">
            <Icon className="h-4 w-4" aria-hidden />
          </div>
        )}
        <div className="min-w-0">
          <Heading id={id} className="text-sm font-semibold text-foreground">
            {title}
          </Heading>
          {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
        </div>
      </div>
      {action && (
        <div
          className={cn(
            "flex flex-shrink-0 flex-wrap gap-2",
            stackActionOnMobile && "w-full sm:w-auto",
          )}
        >
          {action}
        </div>
      )}
    </div>
  );
}
