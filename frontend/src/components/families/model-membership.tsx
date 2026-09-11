import { useState } from "react";
import { Boxes } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { useRouter } from "@/lib/navigation";
import type { ModelRead } from "@/types/models";
import { CreateFamilyDialog } from "./create-dialog";

export function ModelFamilyMembership({
  model,
  editable,
}: {
  model: ModelRead;
  editable: boolean;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  if (model.family)
    return (
      <Link
        href={`/families/${model.family.id}`}
        className="mt-1 inline-flex max-w-full items-center gap-1.5 text-xs text-primary hover:underline"
      >
        <Boxes className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate">
          {model.family.name} · {t(`families.role.${model.family.role}`)}
        </span>
      </Link>
    );
  if (!editable) return null;
  return (
    <>
      <Button variant="ghost" size="xs" className="mt-1" onClick={() => setCreating(true)}>
        <Boxes className="h-3.5 w-3.5" aria-hidden />
        {t("families.create")}
      </Button>
      {creating && (
        <CreateFamilyDialog
          models={[model]}
          onClose={() => setCreating(false)}
          onCreated={(family) => router.push(`/families/${family.id}`)}
        />
      )}
    </>
  );
}
