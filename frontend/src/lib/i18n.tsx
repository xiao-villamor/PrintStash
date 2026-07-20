/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useState } from "react";

const messages = {
  en: {
    "locale.label": "Language",
    "locale.en": "English",
    "locale.es": "Spanish",
    "auth.welcome": "Welcome back",
    "auth.description": "Sign in to manage your PrintStash vault.",
    "auth.username": "Username",
    "auth.password": "Password",
    "auth.remember": "Remember me on this device",
    "auth.signIn": "Sign in",
    "auth.signInWith": "Sign in with {provider}",
    "auth.or": "or",
    "auth.invalid": "Invalid username or password.",
    "auth.failed": "Login failed.",
    "auth.ssoFailed": "Single sign-on failed. Try again or use local login.",
    "auth.expired": "Session expired. Sign in again to continue.",
    "auth.local": "Your credentials stay with your self-hosted server.",
    "auth.tagline": "Your prints, your vault",
    "nav.vault": "Vault",
    "nav.inbox": "Pending",
    "nav.model": "Model",
    "nav.document": "Document",
    "nav.printer": "Printer",
    "nav.printers": "Printers",
    "nav.statistics": "Statistics",
    "nav.settings": "Settings",
    "nav.profiles": "Profiles",
    "nav.signIn": "Sign in",
    "nav.setup": "Setup",
    "nav.search": "Search PrintStash...",
    "nav.clearSearch": "Clear search",
    "nav.stats": "Stats",
    "nav.wiki": "Wiki",
    "nav.more": "More",
    "nav.close": "Close",
    "nav.notifications": "Notifications",
    "nav.tasks": "Tasks",
    "nav.account": "Account",
    "nav.logIn": "Log in",
    "nav.logOut": "Log out",
    "settings.title": "Settings",
    "settings.description": "Vault configuration and display preferences",
    "settings.overview": "Overview",
    "settings.access": "Users & Access",
    "settings.storage": "Storage",
    "settings.imports": "Imports",
    "settings.maintenance": "Maintenance",
    "settings.libraries": "Shared volumes",
    "settings.notifications": "Notifications",
    "settings.sso": "SSO",
    "settings.spoolman": "Spoolman",
    "settings.design": "Design",
    "settings.trash": "Trash",
    "settings.about": "About",
  },
  es: {
    "locale.label": "Idioma",
    "locale.en": "Inglés",
    "locale.es": "Español",
    "auth.welcome": "Te damos la bienvenida",
    "auth.description": "Inicia sesión para gestionar tu bóveda de PrintStash.",
    "auth.username": "Usuario",
    "auth.password": "Contraseña",
    "auth.remember": "Recordarme en este dispositivo",
    "auth.signIn": "Iniciar sesión",
    "auth.signInWith": "Iniciar sesión con {provider}",
    "auth.or": "o",
    "auth.invalid": "Usuario o contraseña no válidos.",
    "auth.failed": "No se pudo iniciar sesión.",
    "auth.ssoFailed": "El inicio de sesión único falló. Inténtalo de nuevo o usa el acceso local.",
    "auth.expired": "La sesión ha caducado. Inicia sesión de nuevo para continuar.",
    "auth.local": "Tus credenciales permanecen en tu servidor autohospedado.",
    "auth.tagline": "Tus impresiones, tu bóveda",
    "nav.vault": "Bóveda",
    "nav.inbox": "Pendientes",
    "nav.model": "Modelo",
    "nav.document": "Documento",
    "nav.printer": "Impresora",
    "nav.printers": "Impresoras",
    "nav.statistics": "Estadísticas",
    "nav.settings": "Ajustes",
    "nav.profiles": "Perfiles",
    "nav.signIn": "Iniciar sesión",
    "nav.setup": "Configuración",
    "nav.search": "Buscar en PrintStash...",
    "nav.clearSearch": "Borrar búsqueda",
    "nav.stats": "Estad.",
    "nav.wiki": "Wiki",
    "nav.more": "Más",
    "nav.close": "Cerrar",
    "nav.notifications": "Notificaciones",
    "nav.tasks": "Tareas",
    "nav.account": "Cuenta",
    "nav.logIn": "Iniciar sesión",
    "nav.logOut": "Cerrar sesión",
    "settings.title": "Ajustes",
    "settings.description": "Configuración de la bóveda y preferencias de visualización",
    "settings.overview": "Resumen",
    "settings.access": "Usuarios y acceso",
    "settings.storage": "Almacenamiento",
    "settings.imports": "Importaciones",
    "settings.maintenance": "Mantenimiento",
    "settings.libraries": "Volúmenes compartidos",
    "settings.notifications": "Notificaciones",
    "settings.sso": "SSO",
    "settings.spoolman": "Spoolman",
    "settings.design": "Diseño",
    "settings.trash": "Papelera",
    "settings.about": "Acerca de",
  },
} as const;

/** Add a catalog here to make a locale available throughout the app. */
export type Locale = keyof typeof messages;
export type MessageKey = keyof typeof messages.en;
export type MessageCatalog = Record<MessageKey, string>;
export const messageCatalogs = messages satisfies Record<Locale, MessageCatalog>;
export const SUPPORTED_LOCALES = Object.keys(messages) as Locale[];
const STORAGE_KEY = "printstash.locale";

type I18nValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, values?: Record<string, string>) => string;
};

const I18nContext = createContext<I18nValue | null>(null);

function initialLocale(): Locale {
  if (typeof window === "undefined") return "en";
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (SUPPORTED_LOCALES.includes(stored as Locale)) return stored as Locale;
  } catch { /* Storage can be unavailable in hardened/private contexts. */ }
  const browserLocale = SUPPORTED_LOCALES.find((candidate) =>
    navigator.language.toLowerCase().startsWith(candidate),
  );
  return browserLocale ?? "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
    try { localStorage.setItem(STORAGE_KEY, locale); } catch { /* non-fatal */ }
  }, [locale]);

  const value = useMemo<I18nValue>(() => ({
    locale,
    setLocale: setLocaleState,
    t(key, values) {
      let message: string = messageCatalogs[locale][key] ?? messageCatalogs.en[key];
      for (const [name, replacement] of Object.entries(values ?? {})) {
        message = message.replaceAll(`{${name}}`, replacement);
      }
      return message;
    },
  }), [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within I18nProvider");
  return value;
}

export function useOptionalI18n(): I18nValue | null {
  return useContext(I18nContext);
}
