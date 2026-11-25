import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";

/** The print entry shared by Overview and Print. */
export function RevisionDeliveryActions({
  secondary = false,
  className,
  onSend,
}: {
  secondary?: boolean;
  className?: string;
  onSend: () => void;
}) {
  return (
    <Button
      size="sm"
      variant={secondary ? "outline" : "default"}
      className={className}
      onClick={onSend}
    >
      {uiText("modelDetail.preparePrint")}
    </Button>
  );
}
