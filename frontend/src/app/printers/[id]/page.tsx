import { notFound } from "next/navigation";
import { PrinterDetailPage } from "@/components/printer-detail";
import { getPrinter } from "@/lib/api";
import type { PrinterRead } from "@/types";

export const revalidate = 0;

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const printerId = Number(id);
  if (Number.isNaN(printerId)) {
    notFound();
  }

  // Server-side fetch so the first paint shows the printer instead of a
  // skeleton; the client falls back to fetching if the API was unreachable.
  let initialPrinter: PrinterRead | undefined;
  try {
    initialPrinter = await getPrinter(printerId);
  } catch (err) {
    const status =
      typeof err === "object" && err !== null && "status" in err
        ? err.status
        : null;
    if (status === 404) {
      notFound();
    }
    initialPrinter = undefined;
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <PrinterDetailPage printerId={printerId} initialPrinter={initialPrinter} />
    </div>
  );
}
