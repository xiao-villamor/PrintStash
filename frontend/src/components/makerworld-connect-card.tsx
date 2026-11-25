"use client";

import { SettingsCard } from "@/components/settings-card";
import { CHROME_EXTENSION_URL } from "@/lib/browser-extension-setup";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Download, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";

const EXTENSION_URL = "https://github.com/xiao-villamor/PrintStash/tree/main/browser-extension";

export function MakerWorldConnectCard() {
  useUiLocale();
  const steps = [
    uiText("Install the extension, then create a pairing code below."),
    uiText("Sign in to MakerWorld and open an individual model page."),
    uiText("Use the extension, then review the staged file in Pending Imports."),
  ];
  return (
    <Localized>
      <SettingsCard
        title={uiText("MakerWorld browser import")}
        description={uiText("Import authenticated models with the PrintStash browser extension")}
        action={
          <>
            <Button asChild variant="ghost" size="sm">
              <a href={EXTENSION_URL} target="_blank" rel="noreferrer">
                {uiText("Open extension source")}
                <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              </a>
            </Button>
            <Button asChild size="sm">
              <a href={CHROME_EXTENSION_URL} target="_blank" rel="noopener noreferrer">
                <Download className="h-3.5 w-3.5" aria-hidden />
                {uiText("Install Chrome extension")}
              </a>
            </Button>
          </>
        }
      >
        <div className="space-y-3 py-4 text-sm">
          <p className="max-w-[80ch] text-muted-foreground">
            {uiText(
              "PrintStash does not store your MakerWorld password, session token, or cookies. The extension downloads the selected package inside your signed-in browser and sends only the model file to Pending Imports.",
            )}
          </p>
          <ol className="space-y-2">
            {steps.map((step, index) => (
              <li key={step} className="flex items-start gap-3">
                <span
                  aria-hidden
                  className="flex size-5.5 shrink-0 items-center justify-center rounded-full bg-foreground/10 font-mono text-2xs"
                >
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          <p className="text-sm text-muted-foreground">
            {uiText(
              "MakerWorld collections are not supported; capture their model pages individually.",
            )}
          </p>
        </div>
      </SettingsCard>
    </Localized>
  );
}
