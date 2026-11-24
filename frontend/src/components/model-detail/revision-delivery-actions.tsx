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
  onSend: (mode: "queue" | "send") => void;
}) {
  return (
    <>
      {!printerless && (
        <Button size={size} onClick={() => onSend("send")}>
          <Printer3D className="size-4" aria-hidden />
          {uiText("Send to printer")}
        </Button>
      )}
      <Button
        size={size}
        variant={printerless ? "default" : "outline"}
        onClick={() => onSend("queue")}
      >
        {uiText("Add to queue")}
      </Button>
      {printerless && (
        <Button asChild size={size} variant="outline">
          <Link href="/printers">
            <Printer3D className="size-4" aria-hidden />
            {uiText("Manage printers")}
          </Link>
        </Button>
      )}
    </>
  );
}
