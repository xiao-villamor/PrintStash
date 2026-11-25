import { useId, useState } from "react";
import { MarkdownView } from "@/components/markdown-view";
import { inputClasses } from "@/components/ui/input";
import { TabBar } from "@/components/ui/tabs";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";

export function ModelDescription({ description }: { description: string }) {
  return (
    <div className="min-w-0 overflow-x-auto">
      <MarkdownView
        source={description}
        className="wrap-anywhere prose-p:whitespace-pre-line prose-p:text-sm prose-headings:mb-1 prose-headings:mt-0 prose-headings:text-sm prose-headings:font-medium prose-headings:text-muted-foreground"
      />
    </div>
  );
}

export function ModelDescriptionEditor({
  description,
  onChange,
}: {
  description: string;
  onChange: (description: string) => void;
}) {
  useUiLocale();
  const id = useId();
  const [mode, setMode] = useState<"write" | "preview">("write");
  const write = uiText("Write");
  const preview = uiText("Preview");

  return (
    <div className="min-w-0 space-y-2">
      <label htmlFor={id} className="block text-sm font-medium">
        {uiText("Description")}
      </label>
      <div className="overflow-hidden rounded border border-border">
        <TabBar
          tabs={[
            { key: "write", label: write },
            { key: "preview", label: preview },
          ]}
          active={mode}
          onChange={setMode}
          className="border-b border-border bg-muted/40 px-2"
          tabClassName="px-3 py-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          activeTabClassName="font-medium text-foreground"
        />
        <div role="tabpanel" aria-label={write} hidden={mode !== "write"}>
          <textarea
            id={id}
            aria-describedby={`${id}-hint`}
            value={description}
            onChange={(event) => onChange(event.target.value)}
            rows={7}
            className={cn(inputClasses, "rounded-none border-0 py-3 [&:is(textarea)]:h-auto")}
            placeholder={uiText("Optional description")}
          />
        </div>
        {mode === "preview" && (
          <div role="tabpanel" aria-label={preview} className="min-h-36 p-3">
            {description.trim() ? (
              <ModelDescription description={description} />
            ) : (
              <p className="text-sm text-muted-foreground">{uiText("multipart.noDescription")}</p>
            )}
          </div>
        )}
      </div>
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {uiText("Markdown supported: headings, lists, links and tables.")}
      </p>
    </div>
  );
}
