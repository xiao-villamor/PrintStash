import { Button } from "@/components/ui/button";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { FileRead } from "@/types";

export function RevisionPicker({
  files,
  selected,
  onSelect,
}: {
  files: readonly FileRead[];
  selected: FileRead | null;
  onSelect: (id: number) => void;
}) {
  useUiLocale();
  if (!selected) return null;
  const recommended = files.find((file) => file.is_recommended);
  return (
    <section className="detail-revision-picker" aria-label={uiText("Inspect revision")}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex min-w-0 flex-wrap gap-1" role="group" aria-label={uiText("Revisions")}>
          {files.map((file) => (
            <Button
              key={file.id}
              size="sm"
              variant="outline"
              aria-pressed={selected.id === file.id}
              className={selected.id === file.id ? "bg-accent text-accent-foreground" : ""}
              onClick={() => onSelect(file.id)}
              title={file.original_filename}
            >
              {uiText("Rev {value1}", {
                value1: String(file.gcode_revision_number ?? file.version),
              })}
              {file.is_recommended && <> · {uiText("Recommended")}</>}
            </Button>
          ))}
        </div>
        {recommended && recommended.id !== selected.id && (
          <Button size="sm" variant="ghost" onClick={() => onSelect(recommended.id)}>
            {uiText("Return to recommended revision")}
          </Button>
        )}
      </div>
      <p className="mt-1 break-all text-xs text-muted-foreground">{selected.original_filename}</p>
    </section>
  );
}
