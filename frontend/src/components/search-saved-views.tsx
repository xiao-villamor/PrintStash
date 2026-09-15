import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { SavedViewSelector } from "@/components/saved-view-selector";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  updateSavedView,
} from "@/lib/api/saved-views";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { SavedViewFilters, SavedViewRead } from "@/types";

export function SearchSavedViews({
  filters,
  onSelect,
  userId,
}: {
  filters: SavedViewFilters;
  onSelect: (filters: SavedViewFilters) => void;
  userId: number;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const key = ["search-saved-views", userId];
  const views = useQuery({ queryKey: key, queryFn: listSavedViews });
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () => createSavedView(name.trim(), filters),
    onSuccess: () => {
      setOpen(false);
      setName("");
      void client.invalidateQueries({ queryKey: key });
    },
  });
  async function change(action: () => Promise<SavedViewRead | void>) {
    try {
      await action();
      await client.invalidateQueries({ queryKey: key });
    } catch (error) {
      toast.error(error);
    }
  }
  return (
    <>
      <SavedViewSelector
        views={views.data ?? []}
        activeId={null}
        onSelect={(view) => onSelect(view.filters)}
        onCreate={() => setOpen(true)}
        onUpdate={(view) => change(() => updateSavedView(view.id, { filters }))}
        onRename={(view, name) => change(() => updateSavedView(view.id, { name }))}
        onDuplicate={(view) =>
          change(() => createSavedView(`${view.name} ${t("aiSearch.copy")}`, view.filters))
        }
        onDelete={(view) => change(() => deleteSavedView(view.id))}
      />
      <Modal open={open} onClose={() => setOpen(false)} title={t("aiSearch.saveView")}>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <label className="block space-y-1 text-sm">
            {t("aiSearch.viewName")}
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={128}
            />
          </label>
          {save.isError && (
            <p role="alert" className="text-sm text-destructive">
              {t("aiSearch.saveViewError")}
            </p>
          )}
          <Button type="submit" loading={save.isPending}>
            {t("aiSearch.saveView")}
          </Button>
        </form>
      </Modal>
    </>
  );
}
