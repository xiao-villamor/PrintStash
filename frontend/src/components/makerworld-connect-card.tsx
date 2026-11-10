"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Download, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";

const EXTENSION_URL = "https://github.com/xiao-villamor/PrintStash/tree/main/browser-extension";
const CHROME_EXTENSION_URL =
  "https://chromewebstore.google.com/detail/printstash/dfeakldahcajangoijfldionahhbeajg";

export function MakerWorldConnectCard() {
  useUiLocale();
  return (
    <Localized>
      <div className="overflow-hidden rounded border border-border bg-card">
        <div className="flex flex-wrap items-start gap-3 px-4 pb-3 pt-4 sm:px-5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center bg-muted text-muted-foreground">
            <Download className="h-4 w-4" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-foreground">
              {uiText("MakerWorld browser import")}
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {uiText("Import authenticated models with the PrintStash browser extension")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline" size="sm">
              <a href={EXTENSION_URL} target="_blank" rel="noreferrer">
                {uiText("Open extension source")}
                <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              </a>
            </Button>
            <Button asChild variant="outline" size="sm">
              <a href={CHROME_EXTENSION_URL} target="_blank" rel="noopener noreferrer">
                {uiText("Install Chrome extension")}
                <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              </a>
            </Button>
          </div>
        </div>

        <div className="space-y-3 px-4 pb-4 text-[13px] sm:pl-16 sm:pr-5">
          <div className="text-foreground">
            <p>
              {uiText(
                "PrintStash does not store your MakerWorld password, session token, or cookies. The extension downloads the selected package inside your signed-in browser and sends only the model file to Pending Imports.",
              )}
            </p>
          </div>
          <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
            <li>
              {uiText("Install the extension and configure this Vault with a named API key.")}
            </li>
            <li>{uiText("Sign in to MakerWorld and open an individual model page.")}</li>
            <li>{uiText("Use the extension, then review the staged file in Pending Imports.")}</li>
          </ol>
          <p className="text-2xs text-muted-foreground">
            {uiText(
              "MakerWorld collections are not supported; capture their model pages individually.",
            )}
          </p>
        </div>
      </div>
    </Localized>
  );
}
