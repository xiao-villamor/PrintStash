import { Link } from "@/lib/navigation";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";

export default function NotFound() {
  return (
    <PageContainer>
      <PageHeader
        title="404"
        description="This page doesn’t exist."
        actions={
          <Button asChild size="xs">
            <Link href="/">Back to vault</Link>
          </Button>
        }
      />
    </PageContainer>
  );
}
