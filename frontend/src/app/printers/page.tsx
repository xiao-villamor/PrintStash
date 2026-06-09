import { PrintersPage } from "@/components/printers-list";

export const revalidate = 0;

export default function Page() {
  return (
    <div className="h-full overflow-y-auto bg-background p-6 pb-24 md:pb-6">
      <PrintersPage />
    </div>
  );
}
