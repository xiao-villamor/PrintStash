"use client";

import {
  BarChart3,
  ExternalLink,
  Keyboard,
  Layers,
  LogOut,
  Settings,
  SlidersHorizontal,
  Star,
  Trash2,
} from "lucide-react";
import { useI18n, useUiLocale } from "@/lib/i18n";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { APP_VERSION } from "@/lib/changelog";
import { localeDefinitions, SUPPORTED_LOCALES, uiText } from "@/lib/locale";
import { useThemeMode, setThemeMode, type ThemeMode } from "@/lib/theme";
import { openKeyboardShortcuts } from "@/lib/keyboard-commands";

export function WorkspaceMenu({
  pathname,
  username,
  printersInQuickAccess,
  isAdmin,
  onNavigate,
  onLogout,
  mobile = false,
}: {
  pathname: string;
  username: string;
  printersInQuickAccess: boolean;
  isAdmin: boolean;
  onNavigate: () => void;
  onLogout: () => void;
  mobile?: boolean;
}) {
  useUiLocale();
  const { locale, setLocale, t } = useI18n();
  const theme = useThemeMode();
  const router = useRouter();
  const searchParams = useSearchParams();
  const themeOptions: { mode: ThemeMode; text: string }[] = [
    { mode: "light", text: uiText("Light") },
    { mode: "dark", text: uiText("Dark") },
    { mode: "system", text: uiText("System") },
  ];
  const items = [
    ...(mobile || !printersInQuickAccess
      ? [
          { href: "/?favorites=true", text: uiText("Favorites"), icon: Star },
          ...(mobile && !printersInQuickAccess
            ? []
            : [{ href: "/profiles", text: t("nav.profiles"), icon: SlidersHorizontal }]),
          { href: "/library/similar", text: t("similarity.title"), icon: Layers },
          ...(isAdmin
            ? [{ href: "/settings?section=trash", text: uiText("settings.trash"), icon: Trash2 }]
            : []),
        ]
      : []),
    ...(isAdmin ? [{ href: "/statistics", text: t("nav.statistics"), icon: BarChart3 }] : []),
    { href: "/settings", text: t("nav.settings"), icon: Settings },
  ];
  return (
    <div className="workspace-menu w-max min-w-65 max-w-85 border border-border bg-popover p-1.5 text-popover-foreground shadow-xl">
      <div className="mb-1 flex items-center gap-2.5 border-b border-border px-2 pb-2.5 pt-2">
        <span
          aria-hidden
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-input bg-accent text-xs font-semibold text-accent-foreground"
        >
          {username.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold" title={username}>
            {username}
          </p>
          <p className="text-xs text-muted-foreground">
            {isAdmin ? uiText("Administrator") : uiText("Member")}{" "}
            {uiText("· v{value1}", { value1: APP_VERSION })}
          </p>
        </div>
      </div>
      {items.map((item) => {
        const target = new URL(item.href, window.location.origin);
        const dedicatedTrash =
          searchParams.get("section") === "trash" && searchParams.get("view") !== "settings";
        const active =
          pathname === target.pathname &&
          (target.search
            ? [...target.searchParams].every(([key, value]) => searchParams.get(key) === value) &&
              (!target.searchParams.has("section") || dedicatedTrash)
            : item.href !== "/settings" || !dedicatedTrash);
        return (
          <a
            key={item.href}
            href={item.href}
            role="menuitem"
            aria-current={active ? "page" : undefined}
            onClick={(event) => {
              if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey
              )
                return;
              event.preventDefault();
              onNavigate();
              router.push(item.href);
            }}
            className={`flex min-h-9 items-center gap-2.5 px-2.5 text-[13px] hover:bg-muted focus-visible:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${active ? "bg-accent text-accent-foreground" : "text-foreground"}`}
          >
            <item.icon
              className="h-4 w-4 shrink-0 text-muted-foreground"
              strokeWidth={1.6}
              aria-hidden
            />
            <span className="flex-1">{item.text}</span>
          </a>
        );
      })}
      <div role="separator" className="mx-0.5 my-1 border-t border-border" />
      <div
        role="group"
        aria-label={uiText("Theme")}
        className="flex min-h-10 items-center justify-between gap-3 pl-2.5 pr-1"
      >
        <span className="text-[13px]">{uiText("Theme")}</span>
        <div className="flex gap-0.5 bg-muted p-0.5">
          {themeOptions.map(({ mode, text }) => (
            <button
              key={mode}
              type="button"
              role="menuitemradio"
              aria-checked={theme === mode}
              onClick={() => setThemeMode(mode)}
              className={`h-6.5 px-2.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${theme === mode ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:bg-card"}`}
            >
              {text}
            </button>
          ))}
        </div>
      </div>
      <div
        role="group"
        aria-label={t("locale.label")}
        className="flex min-h-10 items-center justify-between gap-3 pl-2.5 pr-1"
      >
        <span className="text-[13px]">{t("locale.label")}</span>
        <div className="flex gap-0.5 bg-muted p-0.5">
          {SUPPORTED_LOCALES.map((option) => (
            <button
              key={option}
              type="button"
              role="menuitemradio"
              lang={option}
              aria-checked={locale === option}
              onClick={() => setLocale(option)}
              className={`h-6.5 px-2.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${locale === option ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:bg-card"}`}
            >
              {localeDefinitions[option].name}
            </button>
          ))}
        </div>
      </div>
      <div role="separator" className="mx-0.5 my-1 border-t border-border" />
      <a
        href="https://www.printstash.org/docs/"
        role="menuitem"
        target="_blank"
        rel="noopener noreferrer"
        onClick={onNavigate}
        className="flex min-h-9 items-center gap-2.5 px-2.5 text-[13px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <ExternalLink className="h-4 w-4 text-muted-foreground" strokeWidth={1.6} aria-hidden />
        <span className="flex-1">{uiText("Docs")}</span>
        <small className="text-xs text-muted-foreground">{uiText("printstash.org")}</small>
      </a>
      {mobile && (
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            onNavigate();
            openKeyboardShortcuts();
          }}
          className="flex min-h-9 w-full items-center gap-2.5 px-2.5 text-left text-[13px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <Keyboard className="h-4 w-4 text-muted-foreground" strokeWidth={1.6} aria-hidden />
          {uiText("Keyboard shortcuts")}
        </button>
      )}
      <div role="separator" className="mx-0.5 my-1 border-t border-border" />
      <button
        type="button"
        role="menuitem"
        onClick={onLogout}
        className="flex min-h-9 w-full items-center gap-2.5 px-2.5 text-left text-[13px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <LogOut className="h-4 w-4 text-muted-foreground" strokeWidth={1.6} aria-hidden />
        {t("nav.logOut")}
      </button>
    </div>
  );
}
