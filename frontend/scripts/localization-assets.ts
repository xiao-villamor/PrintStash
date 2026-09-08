import type { Plugin } from "vite";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { localeDefinitions } from "../src/locales/catalogs.ts";

/** Shell assets use the same registry as React, including when the API is offline. */
export function createLocalizationAssets(): Map<string, string> {
  const manifest: {
    name: string;
    short_name: string;
    description: string;
    id: string;
    start_url: string;
    scope: string;
    display: string;
    display_override: string[];
    background_color: string;
    theme_color: string;
    icons: { src: string; sizes: string; type: string; purpose: string }[];
  } = JSON.parse(
    readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), "../public/manifest.webmanifest"),
      "utf8",
    ),
  );
  const assets = new Map<string, string>();
  const shell = Object.fromEntries(
    Object.entries(localeDefinitions).map(([locale, definition]) => {
      const messages = definition.messages;
      assets.set(
        `/manifest.${locale}.webmanifest`,
        JSON.stringify({
          ...manifest,
          lang: locale,
          dir: definition.direction,
          description: messages["shell.description"],
        }),
      );
      return [
        locale,
        {
          direction: definition.direction,
          messages: {
            "shell.description": messages["shell.description"],
            "shell.offlineTitle": messages["shell.offlineTitle"],
            "shell.offlineHeading": messages["shell.offlineHeading"],
            "shell.offlineHelp": messages["shell.offlineHelp"],
            "shell.retry": messages["shell.retry"],
          },
        },
      ];
    }),
  );
  assets.set(
    "/locale-shell.js",
    `(() => {
    const catalogs = ${JSON.stringify(shell).replaceAll("<", "\\u003c")};
    function apply(requested) {
      let saved = "en";
      try { saved = localStorage.getItem("printstash.locale") || "en"; } catch {}
      const locale = Object.hasOwn(catalogs, requested) ? requested : Object.hasOwn(catalogs, saved) ? saved : "en";
      const catalog = catalogs[locale];
      document.documentElement.lang = locale;
      document.documentElement.dir = catalog.direction;
      for (const element of document.querySelectorAll("[data-message]")) {
        const text = catalog.messages[element.dataset.message];
        if (text === undefined) continue;
        if (element.tagName === "META") element.setAttribute("content", text);
        else element.textContent = text;
      }
      const manifest = document.querySelector('link[rel="manifest"]');
      if (manifest) manifest.setAttribute("href", "/manifest." + locale + ".webmanifest");
    }
    apply();
    document.querySelector("[data-offline-retry]")?.addEventListener("click", () => location.reload());
    window.addEventListener("printstash:locale", event => apply(event.detail));
    window.addEventListener("storage", event => { if (event.key === "printstash.locale" || event.key === null) apply(); });
  })();\n`,
  );
  return assets;
}

export function localizationAssets(): Plugin {
  const assets = createLocalizationAssets();
  return {
    name: "printstash-localization-assets",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = request.url?.split("?")[0] ?? "";
        const source = assets.get(pathname);
        if (source === undefined) return next();
        response.setHeader(
          "Content-Type",
          pathname.endsWith(".js") ? "text/javascript" : "application/manifest+json",
        );
        response.end(source);
      });
    },
    generateBundle() {
      for (const [url, source] of assets)
        this.emitFile({ type: "asset", fileName: url.slice(1), source });
    },
  };
}
