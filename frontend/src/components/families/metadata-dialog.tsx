import { useId, useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import {
  removeFamilyCover,
  updateFamily,
  uploadFamilyCover,
  moveFamilyModels,
  tagFamilyModels,
} from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { useCollections } from "@/lib/queries";
import type { FamilyRead } from "@/types/families";
import { FamilyCoverPicker } from "./cover-picker";

export function FamilyMetadataDialog({
  family,
  onClose,
  onSaved,
}: {
  family: FamilyRead;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const collections = useCollections();
  const [name, setName] = useState(family.name);
  const [description, setDescription] = useState(family.description ?? "");
  const [collection, setCollection] = useState(family.collection_id?.toString() ?? "");
  const [cover, setCover] = useState(family.cover_model_id?.toString() ?? "");
  const [url, setUrl] = useState(family.cover_image_url ?? "");
  const [coverMode, setCoverMode] = useState(
    family.cover_image_uploaded ? "upload" : family.cover_image_url ? "url" : "member",
  );
  const [version, setVersion] = useState(family.version);
  const [uploaded, setUploaded] = useState(family.cover_image_uploaded);
  const [coverOpen, setCoverOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={t("families.edit")}
      className="max-w-xl"
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !name.trim()) return;
          setBusy(true);
          setError(null);
          try {
            await updateFamily(family.id, {
              version,
              name: name.trim(),
              description: description.trim() || null,
              collection_id: collection ? Number(collection) : null,
              ...(coverMode === "member"
                ? { cover_model_id: cover ? Number(cover) : null, cover_image_url: null }
                : coverMode === "url"
                  ? { cover_image_url: url.trim() || null, cover_model_id: null }
                  : {}),
            });
            onSaved();
            onClose();
          } catch (cause) {
            setError(userMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label htmlFor={`${id}-name`} className="block space-y-1.5 text-sm font-medium">
              {t("families.name")}
              <Input
                id={`${id}-name`}
                value={name}
                required
                maxLength={255}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label htmlFor={`${id}-collection`} className="block space-y-1.5 text-sm font-medium">
              {t("families.collection")}
              <select
                id={`${id}-collection`}
                className={inputClasses}
                value={collection}
                onChange={(event) => setCollection(event.target.value)}
              >
                <option value="">{t("families.unfiled")}</option>
                {collections.data
                  ?.filter((item) => item.effective_role !== "view")
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.path}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <label htmlFor={`${id}-description`} className="block space-y-1.5 text-sm font-medium">
            {t("families.description")}
            <textarea
              id={`${id}-description`}
              className={`${inputClasses} min-h-20 resize-y`}
              rows={3}
              maxLength={1_000_000}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <details
            className="rounded-md border border-border p-3"
            onToggle={(event) => setCoverOpen(event.currentTarget.open)}
          >
            <summary className="cursor-pointer text-sm font-medium">{t("families.cover")}</summary>
            <div className="mt-3 space-y-3">
              <select
                aria-label={t("families.cover")}
                className={inputClasses}
                value={coverMode}
                onChange={(event) => setCoverMode(event.target.value)}
              >
                <option value="member">{t("families.members")}</option>
                <option value="upload">{t("families.uploadCover")}</option>
                <option value="url">{t("families.coverUrl")}</option>
              </select>
              {coverOpen && coverMode === "member" && (
                <FamilyCoverPicker familyId={family.id} value={cover} onChange={setCover} />
              )}
              {coverMode === "url" && (
                <Input
                  aria-label={t("families.coverUrl")}
                  type="url"
                  maxLength={2048}
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://"
                />
              )}
              {coverMode === "upload" && (
                <>
                  <Input
                    aria-label={t("families.uploadCover")}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    onChange={async (event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (!file) return;
                      setBusy(true);
                      setError(null);
                      try {
                        const result = await uploadFamilyCover(family.id, version, file);
                        setVersion(result.version);
                        setUploaded(true);
                        onSaved();
                      } catch (cause) {
                        setError(userMessage(cause));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  />
                  {uploaded && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        setBusy(true);
                        setError(null);
                        try {
                          const result = await removeFamilyCover(family.id, version);
                          setVersion(result.version);
                          setUploaded(false);
                          setCoverMode("member");
                          setCover("");
                          onSaved();
                        } catch (cause) {
                          setError(userMessage(cause));
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      {t("families.removeCover")}
                    </Button>
                  )}
                </>
              )}
            </div>
          </details>
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
          <Button
            type="submit"
            loading={busy}
            disabled={!name.trim() || (coverMode === "upload" && !uploaded)}
          >
            {t("families.save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function FamilyBulkDialog({
  family,
  mode,
  onClose,
  onSaved,
}: {
  family: FamilyRead;
  mode: "tags" | "collection";
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const collections = useCollections();
  const title = t(mode === "tags" ? "families.bulkTags" : "families.bulkCollection");
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
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
            if (mode === "tags")
              await tagFamilyModels(family.id, family.version, [
                ...new Set(
                  value
                    .split(",")
                    .map((tag) => tag.trim())
                    .filter(Boolean),
                ),
              ]);
            else await moveFamilyModels(family.id, family.version, value);
            onSaved();
            onClose();
          } catch (cause) {
            setError(userMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="text-sm text-muted-foreground">{t("families.bulkHelp")}</p>
        {mode === "tags" ? (
          <Input
            aria-label={t("families.tags")}
            placeholder={t("families.tagPlaceholder")}
            value={value}
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
          />
        ) : (
          <select
            aria-label={t("families.collection")}
            className={inputClasses}
            value={value}
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
          >
            <option value="">{t("families.unfiled")}</option>
            {collections.data
              ?.filter((item) => item.effective_role !== "view")
              .map((item) => (
                <option key={item.id} value={item.path}>
                  {item.path}
                </option>
              ))}
          </select>
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
          <Button type="submit" loading={busy} disabled={mode === "tags" && !value.trim()}>
            {t("families.save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
