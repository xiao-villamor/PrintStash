"use client";

import { ExternalLink, Puzzle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";

const EXTENSION_URL = "https://github.com/xiao-villamor/PrintStash/tree/main/browser-extension";

export function MakerWorldConnectCard() {
  return (
    <Localized>
      <div className="overflow-hidden rounded border border-border bg-card">
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-4 sm:px-6 sm:py-5 lg:px-8">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">MakerWorld browser import</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Import authenticated models with the PrintStash browser extension
            </p>
          </div>
          <Puzzle className="h-5 w-5 flex-shrink-0 text-muted-foreground" aria-hidden />
        </div>

        <div className="space-y-4 p-3 sm:p-4 lg:p-6">
          <div className="flex items-start gap-2 text-sm text-foreground">
            <ShieldCheck className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" aria-hidden />
            <p>
              PrintStash does not store your MakerWorld password, session token, or cookies. The
              extension downloads the selected package inside your signed-in browser and sends only
              the model file to Pending Imports.
            </p>
          </div>
          <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
            <li>Install the extension and configure this Vault with a named API key.</li>
            <li>Sign in to MakerWorld and open an individual model page.</li>
            <li>Use the extension, then review the staged file in Pending Imports.</li>
          </ol>
          <p className="text-2xs text-muted-foreground">
            MakerWorld collections are not supported; capture their model pages individually.
          </p>
          <Button asChild variant="outline" size="sm">
            <a href={EXTENSION_URL} target="_blank" rel="noreferrer">
              Open extension source
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </a>
          </Button>
        </div>
      </div>
    </Localized>
  );
}
