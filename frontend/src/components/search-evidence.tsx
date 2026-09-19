import type { SearchEvidence } from "@/types/search";
import { useI18n } from "@/lib/i18n";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { Box } from "lucide-react";
import { cn } from "@/lib/utils";

export function SearchModelPreview({
  path,
  large = false,
}: {
  path: string | null | undefined;
  large?: boolean;
}) {
  const source = useAuthenticatedAssetUrl(path);
  const frame = large ? "aspect-[4/3] w-full" : "h-16 w-16 shrink-0";
  return source ? (
    <img
      src={source}
      alt=""
      loading="lazy"
      className={cn(frame, "rounded-md bg-muted/30 object-contain")}
    />
  ) : large ? (
    <div
      className={cn(
        frame,
        "flex items-center justify-center rounded-md bg-muted/30 text-muted-foreground",
      )}
      aria-hidden
    >
      <Box className="h-10 w-10" />
    </div>
  ) : null;
}

export function SearchEvidenceList({ evidence }: { evidence: SearchEvidence[] }) {
  const { t } = useI18n();
  const groups = new Map<string, SearchEvidence[]>();
  for (const match of evidence) {
    const key = JSON.stringify([match.field, match.text, match.ranges]);
    const group = groups.get(key) ?? [];
    group.push(match);
    groups.set(key, group);
  }
  return (
    <>
      {[...groups.entries()].map(([key, group]) => (
        <div key={key} className="mt-2 max-w-prose">
          <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            {group.map((match) => (
              <span key={match.leg}>
                {t(
                  match.leg === "lexical"
                    ? "aiSearch.keywordMatch"
                    : match.leg === "thumbnail"
                      ? "aiSearch.appearanceMatch"
                      : match.leg === "multiview" || match.leg === "point_cloud"
                        ? "aiSearch.shapeMatch"
                        : "aiSearch.semanticMatch",
                )}
              </span>
            ))}
          </p>
          {group[0].text && (
            <p className="mt-1 break-words text-sm leading-relaxed text-foreground">
              <EvidenceText evidence={group[0]} />
            </p>
          )}
        </div>
      ))}
    </>
  );
}

/** API offsets count Unicode code points. React escapes all excerpt content. */
export function EvidenceText({ evidence }: { evidence: SearchEvidence }) {
  const characters = Array.from(evidence.text);
  const parts: { text: string; highlighted: boolean; start: number }[] = [];
  let after = 0;
  for (const [start, end] of evidence.ranges) {
    if (start < after || end <= start || end > characters.length) continue;
    if (start > after)
      parts.push({
        text: characters.slice(after, start).join(""),
        highlighted: false,
        start: after,
      });
    parts.push({ text: characters.slice(start, end).join(""), highlighted: true, start });
    after = end;
  }
  parts.push({ text: characters.slice(after).join(""), highlighted: false, start: after });
  return (
    <>
      {parts.map((part) =>
        part.highlighted ? (
          <mark key={part.start} className="rounded-sm bg-primary-soft text-foreground">
            {part.text}
          </mark>
        ) : (
          <span key={part.start}>{part.text}</span>
        ),
      )}
    </>
  );
}
