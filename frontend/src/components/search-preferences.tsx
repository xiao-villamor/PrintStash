import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { saveSearchPreferences } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";
import type { SearchPreferences as Preferences } from "@/types/search";

export function SearchPreferences({ value, userId }: { value: Preferences; userId: number }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState(value.nl_filters_enabled);
  const [timezone, setTimezone] = useState(value.timezone ?? "");
  const save = useMutation({
    mutationFn: () =>
      saveSearchPreferences({ nl_filters_enabled: enabled, timezone: timezone.trim() || null }),
    onSuccess: (result) => {
      client.setQueryData(["search-preferences", userId], result);
      setOpen(false);
    },
  });
  if (!value.available) return null;
  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          setEnabled(value.nl_filters_enabled);
          setTimezone(value.timezone ?? "");
          setOpen(true);
        }}
      >
        {t("aiSearch.personalSettings")}
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title={t("aiSearch.personalSettings")}>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <p className="text-sm text-muted-foreground">
            {t("aiSearch.parseDisclosure", { host: value.endpoint_host ?? "" })}
          </p>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={enabled}
              onChange={setEnabled}
              ariaLabel={t("aiSearch.personalOptIn")}
            />
            {t("aiSearch.personalOptIn")}
          </label>
          <label className="block space-y-1 text-sm">
            {t("aiSearch.personalTimezone")}
            <Input
              maxLength={128}
              placeholder={value.effective_timezone}
              value={timezone}
              onChange={(event) => setTimezone(event.target.value)}
            />
          </label>
          {save.isError && (
            <p role="alert" className="text-sm text-destructive">
              {t("aiSearch.preferenceError")}
            </p>
          )}
          <Button type="submit" loading={save.isPending}>
            {t("aiSearch.savePreferences")}
          </Button>
        </form>
      </Modal>
    </>
  );
}
