import { Button, PageContainer, PageHeader } from "@printstash/ui";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

/** Eager recovery UI remains available when a deferred route cannot load. */
export function RouteError() {
  useUiLocale();
  return (
    <PageContainer>
      <PageHeader
        title={uiText("Page unavailable")}
        description={uiText(
          "Something went wrong in this browser tab. Reload the page and try again.",
        )}
        actions={<Button onClick={() => window.location.reload()}>{uiText("Reload page")}</Button>}
      />
      <a className="text-primary underline" href="/">
        {uiText("Back to vault")}
      </a>
    </PageContainer>
  );
}
