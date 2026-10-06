import { Suspense, useState, type ReactNode } from "react";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";

/** Mount on first opening, then retain the dialog so its ordinary exit still runs. */
export function DeferredDialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const [activated, setActivated] = useState(open);
  if (open && !activated) setActivated(true);
  if (!activated && !open) return null;
  return (
    <Suspense
      fallback={
        open ? (
          <Modal open title={title} onClose={onClose}>
            <div aria-busy="true">
              <Skeleton className="h-32 w-full" />
            </div>
          </Modal>
        ) : null
      }
    >
      {children}
    </Suspense>
  );
}
