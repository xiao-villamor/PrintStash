import { useEffect, useRef, useState } from "react";
import { Link } from "@/lib/link";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { uiText } from "@/lib/locale";
import { userMessage } from "@/lib/errors";
import { getSessionVersion } from "@/lib/session-transport";
import type { LibraryBatchOutcome } from "@/features/library/batch-edits";

/** An interrupted command is a receipt to review, never a new remote cache. */
export function LibraryBatchRecovery({
  receipt,
  intent,
  models,
  onClose,
  onChanged,
}: {
  receipt: LibraryBatchOutcome & { undo: (() => Promise<LibraryBatchOutcome>) | null };
  intent: string[];
  models: { id: number; name: string }[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [undo, setUndo] = useState<LibraryBatchOutcome | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  async function undoConfirmed() {
    if (pending || undo || error || !receipt.undo) return;
    const session = getSessionVersion();
    setPending(true);
    try {
      const outcome = await receipt.undo();
      if (!alive.current || session !== getSessionVersion()) return;
      setUndo(outcome);
      onChanged();
    } catch (cause) {
      if (!alive.current || session !== getSessionVersion()) return;
      setError(userMessage(cause));
    } finally {
      if (alive.current && session === getSessionVersion()) setPending(false);
    }
  }

  return (
    <Modal
      open
      variant="confirmation"
      dismissible={!pending}
      onClose={onClose}
      title={uiText("Batch needs review")}
      description={uiText(
        "Some changes could not be confirmed. Review the current models before making a new selection.",
      )}
      footer={
        <>
          {receipt.result.succeeded_count > 0 && receipt.undo && (
            <Button
              variant="outline"
              loading={pending}
              disabled={undo !== null || error !== null}
              onClick={() => void undoConfirmed()}
            >
              {uiText("Undo confirmed changes")}
            </Button>
          )}
          <Button variant="outline" disabled={pending} onClick={onClose}>
            {uiText("Discard remaining batch")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <div>
          {intent.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
        <Outcome outcome={receipt} models={models} />
        {undo && (
          <section aria-label={uiText("Undo result")} className="space-y-2">
            <h3 className="font-medium">{uiText("Undo result")}</h3>
            <Outcome outcome={undo} models={models} />
          </section>
        )}
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}

function Outcome({
  outcome,
  models,
}: {
  outcome: LibraryBatchOutcome;
  models: { id: number; name: string }[];
}) {
  const { result, completion } = outcome;
  const names = new Map(models.map((model) => [model.id, model.name]));
  const links = (ids: number[]) => (
    <ul className="max-h-48 overflow-y-auto space-y-1">
      {ids.map((id) => {
        const name = names.get(id);
        if (name === undefined) throw new Error("Missing batch review identity");
        return (
          <li key={id}>
            <Link href={`/models/${id}`} className="text-primary underline">
              {name}
            </Link>
          </li>
        );
      })}
    </ul>
  );
  return (
    <div className="space-y-2">
      <p>{uiText("{value1} confirmed", { value1: String(result.succeeded_count) })}</p>
      {result.failed_count > 0 && (
        <p>{uiText("{value1} skipped", { value1: String(result.failed_count) })}</p>
      )}
      {completion.status === "interrupted" && (
        <>
          <p role="alert" className="text-destructive">
            {userMessage(completion.error)}
          </p>
          <p>
            {uiText("{value1} unconfirmed", { value1: String(completion.unconfirmedIds.length) })}
          </p>
          {links(completion.unconfirmedIds)}
          <p>
            {uiText("{value1} not attempted", { value1: String(completion.unattemptedIds.length) })}
          </p>
          {links(completion.unattemptedIds)}
        </>
      )}
    </div>
  );
}
