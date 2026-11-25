import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { uiText } from "@/lib/locale";
import { useComboboxNav } from "@/lib/use-combobox-nav";

interface Option<T> {
  value: T;
  label: string;
  description?: string;
  disabled?: boolean;
}

type OptionPickerProps<T> = {
  label: string;
  options: readonly Option<T>[];
  placeholder?: string;
  searchPlaceholder?: string;
  disabled?: boolean;
} & (
  | {
      multiple?: false;
      value: T | null;
      onChange: (value: T) => void;
      other?: { label: string; selected: boolean; onSelect: () => void };
    }
  | {
      multiple: true;
      value: readonly T[];
      onChange: (values: T[]) => void;
      other?: never;
    }
);

export function OptionPicker<T extends string | number>(props: OptionPickerProps<T>) {
  const { label, options, placeholder, searchPlaceholder, other, disabled } = props;
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const term = query.trim().toLocaleLowerCase();
  const matches = options.filter(({ label, description }) =>
    `${label} ${description ?? ""}`.toLocaleLowerCase().includes(term),
  );
  function isSelected(value: T) {
    return props.multiple ? props.value.includes(value) : value === props.value && !other?.selected;
  }
  const selected = options.filter((option) => isSelected(option.value));
  const selectionLabel =
    selected.length > 1 ? selected.map((option) => option.label).join(", ") : selected[0]?.label;

  function closeAndFocus() {
    setOpen(false);
    trigger.current?.focus();
  }

  function choose(option: Option<T>) {
    if (option.disabled) return;
    if (props.multiple) {
      props.onChange(
        isSelected(option.value)
          ? props.value.filter((value) => value !== option.value)
          : [...props.value, option.value],
      );
      return;
    }
    props.onChange(option.value);
    closeAndFocus();
  }

  const nav = useComboboxNav(matches.length, {
    onSelect: (index) => choose(matches[index]),
    onCommitInput: () => {
      if (matches.length === 1) choose(matches[0]);
    },
  });
  const activeId = nav.inputProps["aria-activedescendant"];
  useEffect(() => {
    if (open && activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeId, open]);

  function toggle(next: boolean) {
    if (next) {
      setQuery("");
      nav.setActiveIndex(-1);
    }
    setOpen(next);
  }

  return (
    <div className="min-w-0 space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <DropdownMenu
        open={open}
        onOpenChange={toggle}
        returnFocusRef={trigger}
        portal
        field
        align="start"
        role="dialog"
        aria-label={label}
        contentClassName="overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-lg"
        trigger={
          <Button
            id={id}
            ref={trigger}
            type="button"
            variant="outline"
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-describedby={`${id}-selection`}
            disabled={disabled}
            className="dropdown-field w-full min-w-0 justify-between font-normal"
            onClick={() => toggle(!open)}
          >
            <span
              id={`${id}-selection`}
              className="min-w-0 flex-1 truncate text-left"
              title={selectionLabel}
            >
              {other?.selected ? other.label : (selectionLabel ?? placeholder ?? uiText("Select"))}
            </span>
            <ChevronDown size={16} className="shrink-0 text-muted-foreground" aria-hidden />
          </Button>
        }
      >
        <div className="border-b border-border p-2">
          <div className="relative">
            <Search
              size={16}
              className="pointer-events-none absolute left-2.5 top-2.5 text-muted-foreground"
              aria-hidden
            />
            <Input
              {...nav.inputProps}
              aria-expanded={open}
              autoFocus={open}
              aria-label={uiText("Search")}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                nav.setActiveIndex(-1);
              }}
              placeholder={searchPlaceholder ?? uiText("Search")}
              className="pl-8"
            />
          </div>
        </div>
        <div
          id={nav.listboxId}
          role="listbox"
          aria-label={label}
          aria-multiselectable={props.multiple || undefined}
          className="max-h-56 overflow-y-auto p-1"
        >
          {matches.map((option, index) => (
            <button
              key={option.value}
              id={nav.optionId(index)}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={isSelected(option.value)}
              aria-disabled={option.disabled || undefined}
              data-active={nav.activeIndex === index || undefined}
              className="field-option aria-disabled:cursor-disabled aria-disabled:opacity-60"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
            >
              <span className="min-w-0 flex-1 wrap-anywhere">
                <span className="block">{option.label}</span>
                {option.description && (
                  <small className="block text-xs text-muted-foreground">
                    {option.description}
                  </small>
                )}
              </span>
              {isSelected(option.value) && <Check size={15} className="shrink-0" aria-hidden />}
            </button>
          ))}
        </div>
        {matches.length === 0 && (
          <p role="status" className="px-3 py-3 text-sm text-muted-foreground">
            {uiText("No matching options.")}
          </p>
        )}
        {other && (
          <div className="border-t border-border">
            <button
              type="button"
              className="field-option justify-between"
              onClick={() => {
                other.onSelect();
                closeAndFocus();
              }}
            >
              {other.label}
              {other.selected && <Check size={15} aria-hidden />}
            </button>
          </div>
        )}
        {props.multiple && (
          <div className="flex justify-end border-t border-border p-2">
            <Button type="button" size="sm" variant="outline" onClick={closeAndFocus}>
              {uiText("Done")}
            </Button>
          </div>
        )}
      </DropdownMenu>
    </div>
  );
}
