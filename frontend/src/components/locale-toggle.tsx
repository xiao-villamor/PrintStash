import { Languages } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { localeDefinitions, SUPPORTED_LOCALES } from "@/lib/locale";
import { DropdownMenu } from "@/components/ui/dropdown-menu";

export function LocaleToggle() {
  const { locale, setLocale, t } = useI18n();
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => setOpen(!open)}
          aria-label={`${t("locale.label")}: ${localeDefinitions[locale].name}`}
          title={`${t("locale.label")}: ${localeDefinitions[locale].name}`}
          aria-haspopup="menu"
          aria-expanded={open}
          data-menu-trigger
        >
          <Languages className="h-4 w-4" aria-hidden />
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
          className="block w-full rounded-sm px-3 py-2 text-start text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => {
            setLocale(option);
            setOpen(false);
          }}
        >
          {localeDefinitions[option].name}
        </button>
      ))}
    </DropdownMenu>
  );
}
