import { Button } from "@/components/ui/button";
import { Printer3D } from "@/components/ui/printer-3d";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";

export function RevisionDeliveryActions({
  printerless,
  size,
  onSend,
}: {
  printerless: boolean;
  size?: "sm";
  onSend: () => void;
}) {
  return (
    <>
      <Button size={size} onClick={onSend}>
        <Printer3D className="size-4" aria-hidden />
        {uiText("modelDetail.preparePrint")}
      </Button>
      {printerless && (
        <Button asChild size={size} variant="outline">
          <Link href="/printers">{uiText("Manage printers")}</Link>
        </Button>
      )}
    </>
  );
}
