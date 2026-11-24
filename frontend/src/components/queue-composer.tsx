import { useId, type ReactNode } from "react";
import { FileBox, GitBranch, Layers3, ShieldCheck, Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { inputClasses } from "@/components/ui/input";
import { Printer3D } from "@/components/ui/printer-3d";
import { RadioOption } from "@/components/ui/radio-option";
import { uiText } from "@/lib/locale";
import type { CompatibilityPolicy, JobPriority, RoutingStrategy } from "@/types";
import "./fleet-queue-workspace.css";

const priorityLabels = { low: "Low", normal: "Normal", rush: "Rush" } as const;

export function QueueRevisionPanel({ children }: { children: ReactNode }) {
  return (
    <div className="queue-revision-choice">
      <FileBox size={20} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function QueueTargetGroupField({
  groups,
  value,
  onChange,
  disabled,
}: {
  groups: readonly string[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="min-w-0 space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {uiText("Target group")}
      </label>
      <select
        id={id}
        className={inputClasses}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || groups.length === 0}
      >
        <option value="">{uiText("Any group")}</option>
        {groups.map((group) => (
          <option key={group} value={group}>
            {group}
          </option>
        ))}
      </select>
    </div>
  );
}

export function QueuePriorityField({
  value,
  onChange,
  disabled,
}: {
  value: JobPriority;
  onChange: (value: JobPriority) => void;
  disabled?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="queue-compose-group" disabled={disabled}>
      <legend>{uiText("Priority")}</legend>
      <div className="queue-options">
        {(["low", "normal", "rush"] as const).map((priority) => (
          <RadioOption
            key={priority}
            name={name}
            checked={value === priority}
            onChange={() => onChange(priority)}
          >
            {uiText(priorityLabels[priority])}
          </RadioOption>
        ))}
      </div>
    </fieldset>
  );
}

export function QueueRoutingField({
  value,
  onChange,
  disabled,
}: {
  value: RoutingStrategy;
  onChange: (value: RoutingStrategy) => void;
  disabled?: boolean;
}) {
  const name = useId();
  const options = [
    { value: "manual", label: uiText("Choose printer"), Icon: Printer3D },
    { value: "default", label: uiText("Default printer"), Icon: Star },
    { value: "least_busy", label: uiText("Least busy"), Icon: GitBranch },
  ] as const;
  return (
    <fieldset className="queue-compose-group" disabled={disabled}>
      <legend>{uiText("Routing")}</legend>
      <div className="queue-options">
        {options.map(({ value: strategy, label, Icon }) => (
          <RadioOption
            key={strategy}
            name={name}
            checked={value === strategy}
            onChange={() => onChange(strategy)}
          >
            <span className="flex items-center gap-1.5">
              <Icon size={15} className="shrink-0" aria-hidden />
              {label}
            </span>
          </RadioOption>
        ))}
      </div>
    </fieldset>
  );
}

export function QueueDispatchDetails({
  model,
  revision,
  destination,
  destinationStatus,
  priority,
  policy,
  plate,
  copies,
  children,
}: {
  model?: string;
  revision?: string;
  destination: string;
  destinationStatus?: string;
  priority: JobPriority;
  policy: CompatibilityPolicy;
  plate?: number | null;
  copies?: number;
  children?: ReactNode;
}) {
  return (
    <aside className="queue-ticket" aria-label={uiText("Dispatch details")}>
      <h3>
        <Layers3 size={17} aria-hidden />
        {uiText("Dispatch details")}
      </h3>
      <div className="queue-ticket-route">
        <FileBox size={15} aria-hidden />
        <div>
          <small>{uiText("Revision")}</small>
          <b>{model ?? uiText("Select a saved Revision")}</b>
          {revision && <small>{revision}</small>}
        </div>
        <Printer3D size={15} aria-hidden />
        <div>
          <small>{uiText("Destination")}</small>
          <b>{destination}</b>
          {destinationStatus && <small>{destinationStatus}</small>}
        </div>
      </div>
      <dl>
        <dt>{uiText("Queue position")}</dt>
        <dd>{uiText("End of queue")}</dd>
        <dt>{uiText("Priority")}</dt>
        <dd>{uiText(priorityLabels[priority])}</dd>
        {copies !== undefined && copies > 1 && (
          <>
            <dt>{uiText("Copies")}</dt>
            <dd>{copies}</dd>
          </>
        )}
        {plate != null && (
          <>
            <dt>{uiText("Sliced plate")}</dt>
            <dd>{plate}</dd>
          </>
        )}
      </dl>
      <p>
        <ShieldCheck size={16} aria-hidden />
        {policy === "safe" ? uiText("Require compatible material") : uiText("Allow mismatch")}
      </p>
      {children}
    </aside>
  );
}

export function QueueDialogActions({
  busy,
  canSubmit,
  onClose,
  submit,
}: {
  busy: boolean;
  canSubmit: boolean;
  onClose: () => void;
  submit: { form: string } | { onClick: () => void };
}) {
  return (
    <>
      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={onClose}>
        {uiText("Cancel")}
      </Button>
      <Button
        {...submit}
        type={"form" in submit ? "submit" : "button"}
        size="sm"
        loading={busy}
        disabled={!canSubmit}
      >
        {busy ? uiText("Queuing…") : uiText("Add to queue")}
      </Button>
    </>
  );
}

export function QueueCompatibilityField({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <PrintConfirmationField
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      label={uiText("Allow material or nozzle mismatch")}
    />
  );
}

export function PrintConfirmationField({
  checked,
  onChange,
  disabled,
  label,
  ariaLabel,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
  ariaLabel?: string;
  children?: ReactNode;
}) {
  return (
    <label className="flex min-h-9 cursor-action select-none items-start gap-3 self-start border-t border-border px-2.5 py-3 text-sm font-medium hover:bg-muted has-[:disabled]:pointer-events-none has-[:disabled]:text-muted-foreground">
      <Checkbox
        className="shrink-0"
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        ariaLabel={ariaLabel}
      />
      <span className="min-w-0 space-y-1">
        <span className="block">{label}</span>
        {children}
      </span>
    </label>
  );
}
