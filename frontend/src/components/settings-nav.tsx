import { ChevronLeft, Search, type LucideIcon } from "lucide-react";
import { useId, useState } from "react";
import { Input } from "@/components/ui/input";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";

export interface SettingsNavSection {
  id: string;
  label: string;
  icon: LucideIcon;
}

export interface SettingsNavGroup {
  label: string;
  sections: SettingsNavSection[];
}

interface SettingsNavProps {
  groups: SettingsNavGroup[];
  active: string;
  onChange: (id: string) => void;
  /** `sidebar` lives in the workbench sidebar; `page` is the in-page rail. */
  variant: "sidebar" | "page";
  hidden?: boolean;
  /** Page rail only: the signed-in account and the way back to the Library. */
  account?: { name: string; role: string };
  onBack?: () => void;
  className?: string;
}

/**
 * The one grouped navigation for Settings. The page rail adds a way back to the
 * Library, the signed-in account and a filter over the section names; both
 * variants list the same groups so the 18 sections never need a scrolling
 * tab strip on a desktop screen.
 */
export function SettingsNav({
  groups,
  active,
  onChange,
  variant,
  hidden,
  account,
  onBack,
  className,
}: SettingsNavProps) {
  useUiLocale();
  const [query, setQuery] = useState("");
  const searchId = useId();
  const needle = query.trim().toLowerCase();
  const filtered = groups
    .map((group) => ({
      ...group,
      sections: needle
        ? group.sections.filter((section) => section.label.toLowerCase().includes(needle))
        : group.sections,
    }))
    .filter((group) => group.sections.length > 0);
  const first = filtered[0]?.sections[0];
  const isPage = variant === "page";

  return (
    <nav
      aria-label={uiText("Settings sections")}
      hidden={hidden}
      className={cn(
        "settings-nav",
        isPage
          ? "sticky top-0 hidden self-start lg:block"
          : "h-full w-full overflow-y-auto px-3 pb-4",
        className,
      )}
    >
      {isPage ? (
        <div className="mb-3 space-y-3">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="flex h-8 w-full items-center gap-1.5 rounded-md px-2 text-left text-sm text-muted-foreground transition-[color,background-color] duration-press hover:bg-popover-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronLeft className="size-4 shrink-0" aria-hidden />
              {uiText("Back to library")}
            </button>
          )}
          {account && (
            <div className="flex items-center gap-2.5 px-2">
              <span
                aria-hidden
                className="flex size-7 shrink-0 items-center justify-center rounded-full bg-foreground/10 text-xs font-semibold uppercase text-foreground"
              >
                {account.name.slice(0, 1)}
              </span>
              <span className="min-w-0 leading-tight">
                <span className="block truncate text-sm font-semibold text-foreground">
                  {account.name}
                </span>
                <span className="block truncate text-sm text-muted-foreground">{account.role}</span>
              </span>
            </div>
          )}
          <div className="relative">
            <label htmlFor={searchId} className="sr-only">
              {uiText("Search settings")}
            </label>
            <Search
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id={searchId}
              type="search"
              value={query}
              placeholder={uiText("Search settings")}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && first) {
                  onChange(first.id);
                  setQuery("");
                }
              }}
              className="h-8 rounded-md pl-8 text-sm"
            />
          </div>
        </div>
      ) : (
        <h2 className="flex h-12 items-center px-1 font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
          {uiText("Settings")}
        </h2>
      )}
      {filtered.length === 0 && (
        <p className="px-2 py-3 text-sm text-muted-foreground">{uiText("No settings match")}</p>
      )}
      {filtered.map((group) => (
        <div key={group.label} className="px-1 py-2">
          <p className="px-2 pb-1 font-mono text-2xs font-medium uppercase tracking-wider text-muted-foreground">
            {group.label}
          </p>
          {group.sections.map((section) => {
            const Icon = section.icon;
            const isActive = section.id === active;
            return (
              <button
                key={section.id}
                type="button"
                aria-current={isActive ? "page" : undefined}
                onClick={() => onChange(section.id)}
                className={cn(
                  "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-sm transition-[color,background-color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-popover-hover hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden />
                <span className="truncate">{section.label}</span>
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
