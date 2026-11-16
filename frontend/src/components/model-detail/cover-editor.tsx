import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Image as ImageIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { provenanceOptions } from "@/features/library/provenance";
import { useModelCoverEditing, type PublishModel } from "@/features/library/model-detail";
import { getModelSourceCoverContentPath } from "@/lib/api/provenance";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { ModelRead } from "@/types";

const MAX_COVER_BYTES = 15 * 1024 * 1024;
const COVER_TYPES = ["image/jpeg", "image/png", "image/webp"];

export function ModelCoverEditor({
  model,
  publish,
  capture,
  onClose,
}: {
  model: ModelRead;
  publish: PublishModel;
  capture: (() => Promise<Blob>) | null;
  onClose: () => void;
}) {
  useUiLocale();
  const edit = useModelCoverEditing(model, publish, onClose);
  const input = useRef<HTMLInputElement>(null);
  const [capturing, setCapturing] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const sources = useQuery({ ...provenanceOptions(model.id), enabled: edit.active });
  const canEdit = model.effective_role === "edit" || model.effective_role === "admin";
  const disabled = !canEdit || edit.state.phase !== "ready" || capturing;
  const pending = edit.state.phase === "saving" || capturing;

  function upload(file: File) {
    if (!COVER_TYPES.includes(file.type) || file.size > MAX_COVER_BYTES) {
      setLocalError(uiText("modelCover.invalidImage"));
      return;
    }
    setLocalError(null);
    edit.submit({ kind: "upload", file });
  }

  async function captureView() {
    if (!capture || disabled) return;
    setCapturing(true);
    setLocalError(null);
    try {
      const blob = await capture();
      upload(new File([blob], "model-view.png", { type: "image/png" }));
    } catch {
      setLocalError(uiText("modelCover.captureFailed"));
    } finally {
      setCapturing(false);
    }
  }

  if (!edit.active) return null;
  const current =
    edit.state.phase === "blocked" && edit.state.reviewed ? edit.state.reviewed : model;
  return (
    <Modal open title={uiText("modelCover.title")} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">{uiText("modelCover.description")}</p>
        <ProtectedThumbnail
          path={current.thumbnail_url}
          alt={uiText("modelCover.current")}
          placeholder={<ImageIcon className="m-auto h-12 w-12 text-muted-foreground" />}
          className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-md border bg-muted"
          imageClassName="h-full w-full object-contain"
        />
        <p className="text-sm text-muted-foreground">
          {uiText(current.has_custom_cover ? "modelCover.custom" : "modelCover.automatic")}
        </p>
        {(localError || (edit.state.phase === "ready" && edit.state.rejected)) && (
          <p role="alert" className="text-sm text-destructive">
            {localError ?? uiText("modelCover.saveFailed")}
          </p>
        )}
        {edit.state.phase === "blocked" && (
          <div role="alert" className="space-y-2 rounded-md border bg-muted p-3 text-sm">
            <p>
              {uiText(
                edit.state.problem === "conflict"
                  ? "library.editConflict"
                  : "library.saveUnconfirmed",
              )}
            </p>
            <Button variant="outline" loading={edit.reviewing} onClick={() => void edit.review()}>
              {uiText("library.reviewLatest")}
            </Button>
            {edit.reviewFailed && <p>{uiText("modelCover.reviewFailed")}</p>}
            {edit.state.reviewed && (
              <Button
                onClick={edit.applyReviewed}
                disabled={
                  edit.state.reviewed.effective_role !== "edit" &&
                  edit.state.reviewed.effective_role !== "admin"
                }
              >
                {uiText("modelCover.applyReviewed")}
              </Button>
            )}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <input
            ref={input}
            type="file"
            accept={COVER_TYPES.join(",")}
            className="sr-only"
            aria-label={uiText("modelCover.upload")}
            disabled={disabled}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file) upload(file);
            }}
          />
          <Button disabled={disabled} loading={pending} onClick={() => input.current?.click()}>
            {uiText("modelCover.upload")}
          </Button>
          <Button
            variant="outline"
            disabled={disabled || !capture}
            onClick={() => void captureView()}
          >
            {uiText("modelCover.capture")}
          </Button>
          <Button
            variant="outline"
            disabled={disabled || !model.has_custom_cover}
            onClick={() => edit.submit({ kind: "reset" })}
          >
            {uiText("modelCover.reset")}
          </Button>
        </div>
        <section className="space-y-2 border-t pt-4" aria-label={uiText("modelCover.sources")}>
          <h3 className="text-sm font-semibold">{uiText("modelCover.sources")}</h3>
          {sources.isPending ? (
            <p className="text-sm text-muted-foreground">{uiText("modelCover.loadingSources")}</p>
          ) : sources.isError ? (
            <div className="space-y-2">
              <p role="alert" className="text-sm text-destructive">
                {uiText("modelCover.sourcesFailed")}
              </p>
              <Button variant="outline" onClick={() => void sources.refetch()}>
                {uiText("Retry")}
              </Button>
            </div>
          ) : sources.data.sources.every((source) => source.cover === null) ? (
            <p className="text-sm text-muted-foreground">{uiText("modelCover.noSources")}</p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {sources.data.sources.flatMap((source) =>
                source.cover === null
                  ? []
                  : [
                      <Button
                        key={source.id}
                        variant="outline"
                        disabled={disabled}
                        className="h-auto flex-col p-2"
                        onClick={() => edit.submit({ kind: "source", sourceId: source.id })}
                        aria-label={uiText("modelCover.useSource", { provider: source.provider })}
                      >
                        <ProtectedThumbnail
                          path={
                            getModelSourceCoverContentPath(model.id, source.id) +
                            "?v=" +
                            encodeURIComponent(source.cover.updated_at)
                          }
                          alt={source.provider}
                          placeholder={<ImageIcon className="h-8 w-8" />}
                          className="flex aspect-[4/3] w-full items-center justify-center overflow-hidden"
                          imageClassName="h-full w-full object-contain"
                        />
                        <span className="max-w-full truncate">{source.provider}</span>
                      </Button>,
                    ],
              )}
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}
