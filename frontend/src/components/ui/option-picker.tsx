import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { uiText } from "@/lib/locale";
import { useComboboxNav } from "@/lib/use-combobox-nav";

export function OptionPicker<T extends string | number>({
  label,
  options,
  value,
  onChange,
  placeholder,
  other,
}: {
  label: string;
  options: readonly { value: T; label: string; description?: string }[];
  value: T | null;
  onChange: (value: T) => void;
  placeholder?: string;
  other?: { label: string; selected: boolean; onSelect: () => void };
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const term = query.trim().toLocaleLowerCase();
  const matches = options.filter(({ label, description }) =>
    `${label} ${description ?? ""}`.toLocaleLowerCase().includes(term),
  );
  const selected = options.find((option) => option.value === value);

  function choose(select: () => void) {
    select();
    setOpen(false);
    trigger.current?.focus();
  }

  const nav = useComboboxNav(matches.length, {
    onSelect: (index) => choose(() => onChange(matches[index].value)),
    onCommitInput: () => {
      if (matches.length === 1) choose(() => onChange(matches[0].value));
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
      <label htmlFor={id} className="text-sm font-medium">
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
        contentClassName="overflow-hidden border border-border bg-popover text-popover-foreground shadow-lg"
        trigger={
          <Button
            id={id}
            ref={trigger}
            type="button"
            variant="outline"
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-describedby={`${id}-selection`}
            className="w-full min-w-0 justify-between border-input bg-background font-normal"
            onClick={() => toggle(!open)}
          >
            <span id={`${id}-selection`} className="truncate" title={selected?.description}>
              {other?.selected ? other.label : (selected?.label ?? placeholder ?? uiText("Select"))}
            </span>
            <ChevronDown size={16} className="shrink-0 text-muted-foreground" aria-hidden />
          </Button>
        }
      >
        <div className="relative border-b border-border">
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
            placeholder={uiText("Search")}
            className="border-0 bg-transparent pl-8"
          />
        </div>
        <div
          id={nav.listboxId}
          role="listbox"
          aria-label={label}
          className="max-h-56 overflow-y-auto"
        >
          {matches.map((option, index) => (
            <button
              key={option.value}
              id={nav.optionId(index)}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={option.value === value && !other?.selected}
              data-active={nav.activeIndex === index || undefined}
              className="field-option"
              onClick={() => choose(() => onChange(option.value))}
            >
              <span className="min-w-0 flex-1 wrap-anywhere">
                <span className="block">{option.label}</span>
                {option.description && (
                  <small className="block text-xs text-muted-foreground">
                    {option.description}
                  </small>
                )}
              </span>
              {option.value === value && !other?.selected && <Check size={15} aria-hidden />}
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
              onClick={() => choose(other.onSelect)}
            >
              {other.label}
              {other.selected && <Check size={15} aria-hidden />}
            </button>
          </div>
        )}
      </DropdownMenu>
    </div>
  );
}
