import { AlertCircle, AlertTriangle, CheckCircle2, Info, MoreHorizontal } from "lucide-react";
import * as React from "react";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";

// Consistent section shell used across every settings section: the heading and
// its actions sit above a light bordered box that holds only rows (the Linear
// and Cursor settings pattern). A `danger` card is the Danger zone.
export function SettingsCard({
  id,
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
  /** Anchor for the "On this page" list. */
  id?: string;
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
    <section
      id={id}
      role="group"
      aria-label={title}
      className={cn("settings-card scroll-mt-4", className)}
    >
      {!hideHeader && (
        <SettingsGroupHead
          placement="outside"
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
  placement = "inside",
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  /** Id of the heading, for regions labelled by it. */
  id?: string;
  level?: 2 | 3;
  stackActionOnMobile?: boolean;
  /** `outside` sits above its box (SettingsCard); `inside` is the first row of a box. */
  placement?: "outside" | "inside";
  className?: string;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div
      className={cn(
        "settings-card-head flex justify-between gap-3",
        placement === "outside" ? "mb-2.5 items-end" : "items-start px-5 pb-2 pt-4",
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

/** The `⋯` menu at the end of a row: everything beyond the one visible action. */
export function SettingsRowMenu({
  label,
  items,
  disabled = false,
}: {
  label: string;
  items: { label: string; onSelect: () => void; danger?: boolean; disabled?: boolean }[];
  disabled?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      align="end"
      role="menu"
      portal
      contentClassName="min-w-44 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg"
      trigger={
        <button
          type="button"
          data-menu-trigger
          aria-label={label}
          title={label}
          aria-haspopup="menu"
          aria-expanded={open}
          disabled={disabled}
          onClick={() => setOpen((value) => !value)}
          className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-[color,background-color] duration-press hover:bg-popover-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </button>
      }
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          disabled={item.disabled}
          onClick={() => {
            setOpen(false);
            item.onSelect();
          }}
          className={cn(
            "flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-sm transition-colors duration-press hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
            item.danger ? "text-destructive" : "text-foreground",
          )}
        >
          {item.label}
        </button>
      ))}
    </DropdownMenu>
  );
}

/** A visible single-choice group written as a segmented control (neutral active fill). */
export function SettingsSegmented<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex max-w-full gap-0.5 overflow-x-auto rounded-lg border border-border bg-card p-0.75"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-7.5 shrink-0 whitespace-nowrap rounded-md px-3 text-sm font-medium transition-[color,background-color] duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              selected
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** Single choice written as selectable cards: label, one line of detail and an optional badge. */
export function SettingsChoiceCards<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string; detail?: string; badge?: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="grid gap-2.5 py-4">
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex items-start gap-3 rounded-lg border p-3.5 text-left transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              selected
                ? "border-primary bg-primary/10"
                : "border-border bg-muted/30 hover:bg-popover-hover",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                selected ? "border-primary" : "border-border",
              )}
            >
              {selected && <span className="size-2 rounded-full bg-primary" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
                {option.label}
                {option.badge && (
                  <span className="rounded-full bg-foreground/10 px-2 py-0.5 text-xs font-medium text-foreground/80">
                    {option.badge}
                  </span>
                )}
              </span>
              {option.detail && (
                <span className="mt-0.5 block text-sm text-muted-foreground">{option.detail}</span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
