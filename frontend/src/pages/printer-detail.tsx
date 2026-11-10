import { useParams } from "@tanstack/react-router";

import { PrinterDetailPage } from "@/components/printer-detail";
import { PageContainer } from "@/components/ui/page-container";
import NotFound from "./not-found";

export default function PrinterDetailRoute() {
  const { id } = useParams({ strict: false });
  const printerId = Number(id);
  if (!id || Number.isNaN(printerId)) return <NotFound />;
  return (
    <PageContainer>
      <PrinterDetailPage printerId={printerId} initialPrinter={undefined} />
    </PageContainer>
  );
}
