import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useUiLocale } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";
import type {
  ExternalLibrary,
  ExternalLibraryCollectionMode,
  ExternalLibraryUpdate,
} from "@/types";

const LAYOUTS = ["mirror", "single"] as const satisfies readonly ExternalLibraryCollectionMode[];

function parseLayout(value: string): ExternalLibraryCollectionMode {
  return LAYOUTS.find((layout) => layout === value) ?? "mirror";
}

/** Rename a library source and change how it maps to collections; a mounted
 *  folder can also move. A remote source's place is its connection's, so it is
 *  shown but changed in Settings → Remote storage. */
export function EditLibrarySourceModal({
  source,
  location,
  disabled,
  onSave,
  onClose,
}: {
  source: ExternalLibrary;
  /** Human description of where the source reads (see describeSourceLocation). */
  location: string;
  disabled: boolean;
  onSave: (payload: ExternalLibraryUpdate) => void;
  onClose: () => void;
}) {
  useUiLocale();
  const mounted = (source.source_kind ?? "mounted") === "mounted";
  const [name, setName] = useState(source.name);
  const [folder, setFolder] = useState(source.root_path);
  const [layout, setLayout] = useState(source.collection_mode);
  const [path, setPath] = useState(source.source_prefix ?? "");

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const payload: ExternalLibraryUpdate = {};
    if (name.trim() !== source.name) payload.name = name.trim();
    if (mounted && folder.trim() !== source.root_path) payload.root_path = folder.trim();
    if (!mounted && path.trim() !== (source.source_prefix ?? ""))
      payload.source_prefix = path.trim();
    if (layout !== source.collection_mode) payload.collection_mode = layout;
    if (Object.keys(payload).length > 0) onSave(payload);
    onClose();
  }

  return (
    <Modal open onClose={onClose} title={uiText("Edit source")}>
      <form onSubmit={submit} className="space-y-4">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          {uiText("Source name")}
          <Input
            value={name}
            maxLength={128}
            onChange={(event) => setName(event.target.value)}
            aria-label={uiText("Source name")}
          />
        </label>
        {mounted ? (
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            {uiText("Mounted folder path")}
            <Input
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              aria-label={uiText("Mounted folder path")}
            />
            <span className="text-2xs">
              {uiText("A new folder has to be verified again before it is scanned.")}
            </span>
          </label>
        ) : (
          <>
            <div className="flex flex-col gap-1 text-xs text-muted-foreground">
              {uiText("Remote location")}
              <p className="rounded border border-border bg-muted/40 px-3 py-2 font-mono text-xs text-foreground break-all">
                {location}
              </p>
              <span className="text-2xs">
                {uiText(
                  "The connection's bucket and base folder are set in Settings → Remote storage.",
                )}{" "}
                <Link href="/settings?section=remote-storage" className="font-medium underline">
                  {uiText("Edit connection")}
                </Link>
              </span>
            </div>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {uiText("Path within connection (optional)")}
              <Input
                value={path}
                placeholder={uiText("e.g. production/models")}
                onChange={(event) => setPath(event.target.value)}
                aria-label={uiText("Source path within connection")}
              />
              <span className="text-2xs">
                {uiText(
                  "A source that already indexed files cannot change folder: remove it (your files stay untouched) and add it again.",
                )}
              </span>
            </label>
          </>
        )}
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          {uiText("Collection layout")}
          <select
            className={inputClasses}
            aria-label={uiText("Collection layout")}
            value={layout}
            onChange={(event) => setLayout(parseLayout(event.target.value))}
          >
            <option value="mirror">{uiText("Map subfolders to collections")}</option>
            <option value="single">{uiText("Single collection (flat)")}</option>
          </select>
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            {uiText("Cancel")}
          </Button>
          <Button type="submit" disabled={disabled || name.trim() === ""}>
            {uiText("Save changes")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
