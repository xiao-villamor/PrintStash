import { Check, Languages } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { localeDefinitions, SUPPORTED_LOCALES } from "@/lib/locale";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function LocaleToggle({ menuItem = false }: { menuItem?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      aria-label={t("locale.label")}
      contentClassName="min-w-40 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-lg"
      trigger={
        <Button
          ref={triggerRef}
          type="button"
          variant="ghost"
          size="icon"
          role={menuItem ? "menuitem" : undefined}
          className={menuItem ? "w-full justify-start gap-3 px-3 text-sm" : undefined}
          onClick={() => setOpen(!open)}
          aria-label={`${t("locale.label")}: ${localeDefinitions[locale].name}`}
          title={`${t("locale.label")}: ${localeDefinitions[locale].name}`}
          aria-haspopup="menu"
          aria-expanded={open}
          data-menu-trigger
        >
          <Languages className="h-4 w-4" aria-hidden />
          {menuItem && <span>{`${t("locale.label")}: ${localeDefinitions[locale].name}`}</span>}
        </Button>
      }
    >
      {SUPPORTED_LOCALES.map((option) => (
        <button
          key={option}
          type="button"
          role="menuitemradio"
          aria-checked={option === locale}
          lang={option}
          dir={localeDefinitions[option].direction}
          className={cn(
            "flex w-full items-center justify-between gap-3 rounded-sm px-3 py-2 text-start text-sm transition-[color,background-color,transform] duration-press active:scale-[0.98] hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
            option === locale && "bg-accent text-accent-foreground",
          )}
          onClick={() => {
            setLocale(option);
            setOpen(false);
            triggerRef.current?.focus();
          }}
        >
          <span>{localeDefinitions[option].name}</span>
          {option === locale && <Check className="h-4 w-4 shrink-0" aria-hidden />}
        </button>
      ))}
    </DropdownMenu>
  );
}
