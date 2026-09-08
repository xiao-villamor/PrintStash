import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";

export default function NotFound() {
  useUiLocale();
  return (
    <PageContainer>
      <PageHeader
        title="404"
        description={uiText("This page doesn’t exist.")}
        actions={
          <Button asChild size="xs">
            <Link href="/">{uiText("Back to vault")}</Link>
          </Button>
        }
      />
    </PageContainer>
  );
}
