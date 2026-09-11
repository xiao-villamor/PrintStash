import { useId, useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, inputClasses } from "@/components/ui/input";
import {
  addFamilyMember,
  getFamily,
  moveFamilyMember,
  setFamilyCanonical,
  updateFamilyMember,
} from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { FamilyMemberItem, FamilyRead, MemberRole } from "@/types/families";
import { MEMBER_ROLES } from "@/types/families";
import { FamilyModelPicker, type FamilyPickerModel } from "./model-picker";

export function MemberRoleSelect({
  value,
  onChange,
  label,
}: {
  value: MemberRole;
  onChange: (role: MemberRole) => void;
  label: string;
}) {
  const { t } = useI18n();
  const id = useId();
  return (
    <label htmlFor={id} className="block space-y-1.5 text-sm font-medium">
      {label}
      <select
        id={id}
        className={inputClasses}
        value={value}
        onChange={(event) => {
          const role = MEMBER_ROLES.find((item) => item === event.target.value);
          if (role) onChange(role);
        }}
      >
        {MEMBER_ROLES.map((role) => (
          <option key={role} value={role}>
            {t(`families.role.${role}`)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function AddFamilyMemberDialog({
  family,
  onClose,
  onSaved,
}: {
  family: FamilyRead;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<Map<number, FamilyPickerModel>>(new Map());
  const [role, setRole] = useState<MemberRole>("identical");
  const [review, setReview] = useState<{ source: FamilyRead; destination: FamilyRead } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const model = [...selected.values()][0];
  const moving = !!model?.family;
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={t(review ? "families.moveReview" : "families.addMembers")}
      className="max-w-xl"
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!model || busy) return;
          setBusy(true);
          setError(null);
          try {
            if (model.family && !review) {
              const [source, destination] = await Promise.all([
                getFamily(model.family.id),
                getFamily(family.id),
              ]);
              setReview({ source, destination });
            } else {
              if (review)
                await moveFamilyMember(family.id, {
                  model_id: model.id,
                  role,
                  source_family_id: review.source.id,
                  source_version: review.source.version,
                  destination_version: review.destination.version,
                });
              else await addFamilyMember(family.id, family.version, { model_id: model.id, role });
              onSaved();
              onClose();
            }
          } catch (cause) {
            setError(userMessage(cause));
            setReview(null);
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy} className="space-y-4">
          {review && model ? (
            <div className="space-y-2">
              <p className="text-sm">
                {t("families.moveHelp", {
                  name: model.name,
                  source: review.source.name,
                  destination: review.destination.name,
                })}
              </p>
              {review.source.canonical_model_id === model.id && (
                <p className="text-sm text-muted-foreground">
                  {t("families.moveCanonicalWarning")}
                </p>
              )}
            </div>
          ) : (
            <FamilyModelPicker
              selected={selected}
              allowGrouped
              excludedFamilyId={family.id}
              onToggle={(item) => {
                setSelected(selected.has(item.id) ? new Map() : new Map([[item.id, item]]));
              }}
            />
          )}
          {model && (
            <MemberRoleSelect
              value={role}
              onChange={setRole}
              label={t("families.roleFor", { name: model.name })}
            />
          )}
        </fieldset>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => (review ? setReview(null) : onClose())}
          >
            {t("families.cancel")}
          </Button>
          <Button
            type="submit"
            disabled={
              !model ||
              (review !== null &&
                [review.source, review.destination].some((item) => item.effective_role === "view"))
            }
            loading={busy}
          >
            {t(review ? "families.move" : moving ? "families.moveReview" : "families.addMembers")}
          </Button>
        </div>
        {review &&
          [review.source, review.destination].some((item) => item.effective_role === "view") && (
            <p className="text-xs text-muted-foreground">{t("families.readOnly")}</p>
          )}
      </form>
    </Modal>
  );
}

export function EditFamilyMemberDialog({
  family,
  member,
  onClose,
  onSaved,
}: {
  family: FamilyRead;
  member: FamilyMemberItem;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const canonical = member.role === "canonical";
  const [role, setRole] = useState<MemberRole>(
    member.role === "canonical" ? "identical" : member.role,
  );
  const [note, setNote] = useState(member.transformation_note ?? "");
  const [scale, setScale] = useState(member.scale_factor?.toString() ?? "");
  const [mirrored, setMirrored] = useState(member.mirrored);
  const [verified, setVerified] = useState(member.mirror_verified);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={t("families.memberEdit")}
      className="max-w-lg"
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError(null);
          try {
            const changes = canonical
              ? { transformation_note: note.trim() || null }
              : {
                  transformation_note: note.trim() || null,
                  role,
                  scale_factor: scale.trim() ? Number(scale) : null,
                  mirrored,
                  mirror_verified: verified,
                };
            await updateFamilyMember(family.id, member.id, family.version, changes);
            onSaved();
            onClose();
          } catch (cause) {
            setError(userMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="text-sm font-medium">{member.model.name}</p>
        <fieldset disabled={busy} className="space-y-3">
          {!canonical && (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <MemberRoleSelect value={role} onChange={setRole} label={t("families.role")} />
                <label className="block space-y-1.5 text-sm font-medium" htmlFor={`${id}-scale`}>
                  {t("families.scale")}
                  <Input
                    id={`${id}-scale`}
                    type="number"
                    min="0.000001"
                    step="any"
                    value={scale}
                    onChange={(event) => setScale(event.target.value)}
                    aria-describedby={`${id}-scale-help`}
                  />
                </label>
              </div>
              <p id={`${id}-scale-help`} className="text-xs text-muted-foreground">
                {t("families.scaleHelp")}
              </p>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={mirrored}
                  onChange={(value) => {
                    setMirrored(value === true);
                    setVerified(false);
                  }}
                />
                {t("families.mirrored")}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={verified} onChange={(value) => setVerified(value === true)} />
                {t("families.mirrorVerified")}
              </label>
            </>
          )}
          <label htmlFor={`${id}-note`} className="block space-y-1.5 text-sm font-medium">
            {t("families.note")}
            <textarea
              id={`${id}-note`}
              className={`${inputClasses} min-h-20 resize-y`}
              rows={3}
              maxLength={4096}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
        </fieldset>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            {t("families.cancel")}
          </Button>
          <Button type="submit" loading={busy}>
            {t("families.save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function CanonicalFamilyDialog({
  family,
  member,
  onClose,
  onSaved,
}: {
  family: FamilyRead;
  member: FamilyMemberItem;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [role, setRole] = useState<MemberRole>("identical");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={t("families.setCanonical")}
      className="max-w-md"
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError(null);
          try {
            await setFamilyCanonical(family.id, member.id, role, family.version);
            onSaved();
            onClose();
          } catch (cause) {
            setError(userMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="text-sm font-medium">{member.model.name}</p>
        <p className="text-sm text-muted-foreground">{t("families.canonicalReview")}</p>
        {family.canonical_member_id !== null && (
          <fieldset disabled={busy}>
            <MemberRoleSelect value={role} onChange={setRole} label={t("families.previousRole")} />
          </fieldset>
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
          <Button type="submit" loading={busy}>
            {t("families.setCanonical")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
