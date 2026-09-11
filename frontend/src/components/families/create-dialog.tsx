import { cn } from "@/lib/utils";
import { MEMBER_ROLES } from "@/types/families";
import { useId, useState } from "react";
import { X } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { createFamily } from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { FamilyRead, MemberRole } from "@/types/families";
import { FamilyModelPicker, type FamilyPickerModel } from "./model-picker";

export function CreateFamilyDialog({
  models = [],
  onClose,
  onCreated,
}: {
  models?: FamilyPickerModel[];
  onClose: () => void;
  onCreated: (family: FamilyRead) => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const [name, setName] = useState("");
  const [selected, setSelected] = useState(() => new Map(models.map((model) => [model.id, model])));
  const [canonical, setCanonical] = useState<number | null>(null);
  const [roles, setRoles] = useState<Map<number, MemberRole>>(new Map());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const conflict = [...selected.values()].some((model) => !!model.family);
  const valid =
    !!name.trim() &&
    canonical !== null &&
    selected.has(canonical) &&
    selected.size <= 500 &&
    !conflict;
  const toggle = (model: FamilyPickerModel) => {
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(model.id)) next.delete(model.id);
      else next.set(model.id, model);
      return next;
    });
    if (canonical === model.id) setCanonical(null);
  };
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={t("families.create")}
      className="max-w-2xl"
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!valid || canonical === null || busy) return;
          setBusy(true);
          setError(null);
          try {
            const family = await createFamily({
              name: name.trim(),
              canonical_model_id: canonical,
              members: [...selected.values()].map((model, index) => ({
                model_id: model.id,
                role: roles.get(model.id) ?? "identical",
                sort_order: index,
              })),
            });
            onCreated(family);
          } catch (cause) {
            setError(userMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="text-sm text-muted-foreground">{t("families.createHelp")}</p>
        <div className="space-y-1.5">
          <label htmlFor={id} className="text-sm font-medium">
            {t("families.name")}
          </label>
          <Input
            id={id}
            maxLength={255}
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
        </div>
        <fieldset disabled={busy} className="space-y-3">
          <FamilyModelPicker selected={selected} onToggle={toggle} />
          {selected.size > 0 && (
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">
                {t("families.chooseCanonical", { count: selected.size })}
              </legend>
              <p className="text-xs text-muted-foreground">{t("families.canonicalHelp")}</p>
              <div className="max-h-48 overflow-y-auto rounded-md border border-border">
                {[...selected.values()].map((model) => (
                  <div
                    key={model.id}
                    className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 last:border-b-0"
                  >
                    <label className="flex min-w-0 flex-1 items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name={`${id}-canonical`}
                        value={model.id}
                        checked={canonical === model.id}
                        onChange={() => {
                          setCanonical(model.id);
                          if (!name.trim()) setName(model.name.slice(0, 255));
                        }}
                        className="accent-primary"
                      />
                      <span className="truncate">{model.name}</span>
                    </label>
                    {model.id !== canonical && (
                      <select
                        aria-label={t("families.roleFor", { name: model.name })}
                        value={roles.get(model.id) ?? "identical"}
                        onChange={(event) => {
                          const role = MEMBER_ROLES.find((item) => item === event.target.value);
                          if (role) setRoles((current) => new Map(current).set(model.id, role));
                        }}
                        className={cn(inputClasses, "h-9 w-32 text-xs")}
                      >
                        {MEMBER_ROLES.map((role) => (
                          <option key={role} value={role}>
                            {t(`families.role.${role}`)}
                          </option>
                        ))}
                      </select>
                    )}
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="ghost"
                      aria-label={t("families.removeSelection", { name: model.name })}
                      onClick={() => toggle(model)}
                    >
                      <X className="h-4 w-4" aria-hidden />
                    </Button>
                  </div>
                ))}
              </div>
            </fieldset>
          )}
        </fieldset>
        {conflict && (
          <p role="alert" className="text-sm text-destructive">
            {t("families.createConflict")}
          </p>
        )}
        {selected.size > 500 && (
          <p role="alert" className="text-sm text-destructive">
            {t("families.createLimit")}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            {t("families.cancel")}
          </Button>
          <Button type="submit" loading={busy} disabled={!valid}>
            {t("families.create")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
