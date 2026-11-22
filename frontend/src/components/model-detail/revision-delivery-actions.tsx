import { Button } from "@/components/ui/button";
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
          <Link href="/printers">{uiText("Manage printers")}</Link>
        </Button>
      )}
    </>
  );
}
