import { PrinterDetailPage } from "@/components/printer-detail";

export const revalidate = 0;

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="h-full overflow-y-auto p-6">
      <PrinterDetailPage printerId={Number(id)} />
    </div>
  );
}
