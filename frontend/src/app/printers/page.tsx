import { PrintersPage } from "@/components/printers-list";
import { listPrinters } from "@/lib/api";
import type { PrinterRead } from "@/types";

export const revalidate = 0;

export default async function Page() {
  // Server-side fetch so the first paint shows printers instead of a skeleton.
  // Falls back to client fetching if the API is unreachable during render.
  let initialPrinters: PrinterRead[] | undefined;
  try {
    initialPrinters = await listPrinters();
  } catch {
    initialPrinters = undefined;
  }

  return (
    <div className="h-full overflow-y-auto bg-background p-6 pb-24 md:pb-6">
      <PrintersPage initialPrinters={initialPrinters} />
    </div>
  );
}
