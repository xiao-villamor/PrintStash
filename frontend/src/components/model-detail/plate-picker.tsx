"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Check, Layers, LayoutGrid } from "lucide-react";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { Localized } from "@/components/ui/localized";
import { Button } from "@/components/ui/button";
import { LibraryPreviewPlaceholder } from "@/components/library-card-placeholder";
import "./plate-picker.css";
import type { FilePlate } from "@/types";
import { plateLabel } from "./presentation";

/**
 * Choose which plate of a multi-plate 3MF the viewer shows, or all of them.
 * ``selected`` is the shown plate's number; ``null`` shows the whole build.
 */
export function PlatePicker({
  plates,
  selected,
  onSelect,
}: {
  plates: FilePlate[];
  selected: number | null;
  onSelect: (plate: number | null) => void;
}) {
  useUiLocale();
  return (
    <Localized>
      <div role="group" aria-label={uiText("Plates")} className="model-plate-picker">
        <div className="model-plate-heading">
          <span>
            {uiText("Plates")} <span className="model-plate-count">{plates.length}</span>
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={selected === null}
            onClick={() => onSelect(null)}
            className="model-plate-all"
          >
            <LayoutGrid className="h-4 w-4" aria-hidden />
            {uiText("All plates")}
            <Check className="model-plate-check h-4 w-4" aria-hidden />
          </Button>
        </div>
        <div className="model-plate-options">
          {plates.map((plate) => (
            <Button
              key={plate.number}
              type="button"
              variant="outline"
              aria-pressed={selected === plate.number}
              disabled={plate.object_count === 0}
              onClick={() => onSelect(plate.number)}
              title={plate.name ?? uiText("Plate {value1}", { value1: plateLabel(plate) })}
              className="model-plate-choice"
            >
              <ProtectedThumbnail
                path={plate.image_url}
                alt=""
                fade
                className="model-plate-thumbnail"
                imageClassName="h-full w-full object-contain"
                loadingPlaceholder={<LibraryPreviewPlaceholder />}
                placeholder={<Layers className="h-5 w-5 text-muted-foreground" aria-hidden />}
              />
              <span className="model-plate-copy">
                <span className="model-plate-meta">
                  <span>{uiText("Plate {value1}", { value1: plateLabel(plate) })}</span>
                  <span>{uiText("Objects: {value1}", { value1: String(plate.object_count) })}</span>
                </span>
                {plate.name && <span className="model-plate-name">{plate.name}</span>}
              </span>
              <Check className="model-plate-check h-4 w-4 shrink-0" aria-hidden />
            </Button>
          ))}
        </div>
      </div>
    </Localized>
  );
}
