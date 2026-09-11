import { useState } from "react";
import { Boxes } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";
import type { ModelRead } from "@/types/models";
import { CreateFamilyDialog } from "./create-dialog";
import { JoinFamilyDialog } from "./join-dialog";
import { FamilyMembershipSummary } from "./membership-summary";

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
  const [joining, setJoining] = useState(false);
  if (model.family) return <FamilyMembershipSummary family={model.family} model={model} />;
  if (!editable) return null;
  return (
    <>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <Button variant="ghost" size="xs" onClick={() => setJoining(true)}>
          <Boxes className="h-3.5 w-3.5" aria-hidden />
          {t("families.join")}
        </Button>
        <Button variant="ghost" size="xs" onClick={() => setCreating(true)}>
          {t("families.create")}
        </Button>
      </div>
      {joining && (
        <JoinFamilyDialog
          modelId={model.id}
          onClose={() => setJoining(false)}
          onJoined={(family) => router.push(`/families/${family.id}`)}
        />
      )}
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
