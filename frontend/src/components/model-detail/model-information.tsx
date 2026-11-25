import { InspectorSection, PropertyList, PropertyRow, SoftCard } from "./inspector-section";
import { useLocation } from "@tanstack/react-router";
import { AlignLeft, CalendarDays, ChevronRight, Folder, Tag } from "lucide-react";
import { Link } from "@/lib/link";
import type { EditingBase } from "@/types/editing";
import { ModelTagsField } from "./model-tags-field";
import { Button } from "@/components/ui/button";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { ModelRead, TagRead } from "@/types";
import { ModelDescription } from "./model-description";

/** Display segments for a collection path; a parent repeated by its child adds nothing. */
function collectionDisplaySegments(label: string): string[] {
  return label
    .split("/")
    .filter((name, index, names) => index === names.length - 1 || name !== names[index + 1]);
}

/** Whether a description adds information beyond the Model's own name. */
function describesBeyondName(description: string | null, name: string): boolean {
  const text = description?.trim() ?? "";
  return text !== "" && text.toLocaleLowerCase() !== name.trim().toLocaleLowerCase();
}

/** Model facts in Overview describe the Model independently of the inspected Revision. */
export function ModelInformation({
  model,
  canEdit,
  tagSuggestions,
  onTagsSaved,
  onEditDetails,
}: {
  model: ModelRead;
  canEdit: boolean;
  tagSuggestions: TagRead[];
  onTagsSaved: (tags: string[], base: EditingBase) => void;
  onEditDetails?: () => void;
}) {
  useUiLocale();
  const location = useLocation();
  const segments = model.collection_label ? collectionDisplaySegments(model.collection_label) : [];
  const description = describesBeyondName(model.description, model.name) ? model.description : null;
  return (
    <InspectorSection
      title={uiText("Model details")}
      actions={
        onEditDetails && (
          <Button type="button" variant="ghost" size="xs" onClick={onEditDetails}>
            {uiText("Edit details")}
          </Button>
        )
      }
    >
      <SoftCard>
        <PropertyList>
          <PropertyRow label={uiText("Description")} icon={<AlignLeft />}>
            {description ? (
              <ModelDescription description={description} />
            ) : onEditDetails ? (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                className="-my-1 -ms-2 h-auto min-h-7 px-2 text-sm text-muted-foreground"
                onClick={onEditDetails}
              >
                {uiText("modelDetail.addDescription")}
              </Button>
            ) : (
              <span className="text-muted-foreground">{uiText("None")}</span>
            )}
          </PropertyRow>
          {model.collection && model.collection_label && (
            <PropertyRow label={uiText("Collection")} icon={<Folder />}>
              <Link
                href={`/?c=${encodeURIComponent(model.collection)}`}
                state={location.state}
                aria-label={segments.join(" / ")}
                title={segments.join(" / ")}
                className="inline-flex min-w-0 max-w-full flex-wrap items-baseline gap-x-1.5 rounded-sm underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {segments.map((name, index) => (
                  <span
                    key={`${index}:${name}`}
                    className="flex min-w-0 items-baseline gap-1.5 text-muted-foreground last:text-foreground"
                  >
                    {index > 0 && (
                      <ChevronRight
                        aria-hidden="true"
                        className="mt-1 size-3 shrink-0 self-start"
                      />
                    )}
                    <span className="min-w-0 wrap-anywhere">{name}</span>
                  </span>
                ))}
              </Link>
            </PropertyRow>
          )}
          <PropertyRow label={uiText("Created")} icon={<CalendarDays />}>
            {new Date(model.created_at).toLocaleDateString(currentLocale(), {
              dateStyle: "medium",
            })}
          </PropertyRow>
          <PropertyRow label={uiText("Tags")} icon={<Tag />}>
            <ModelTagsField
              key={model.id}
              model={model}
              suggestions={tagSuggestions}
              canEdit={canEdit}
              onSaved={onTagsSaved}
            />
          </PropertyRow>
        </PropertyList>
      </SoftCard>
    </InspectorSection>
  );
}
