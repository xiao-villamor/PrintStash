import { useParams } from "react-router-dom";

import { PrinterDetailPage } from "@/components/printer-detail";
import NotFound from "./not-found";

export default function PrinterDetailRoute() {
  const { id } = useParams();
  const printerId = Number(id);
  if (!id || Number.isNaN(printerId)) return <NotFound />;
  return (
    <div className="h-full overflow-y-auto p-6">
      <PrinterDetailPage printerId={printerId} initialPrinter={undefined} />
    </div>
  );
}
