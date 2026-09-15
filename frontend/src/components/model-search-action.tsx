import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";

import { getSearchStatus } from "@/lib/api/search";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";

export function ModelSearchAction({
  modelId,
  onSelect,
}: {
  modelId: number;
  onSelect: () => void;
}) {
  const { user } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const status = useQuery({
    queryKey: ["ai-search", "status", user?.id],
    queryFn: getSearchStatus,
    enabled: !!user,
  });
  if (!user || !status.data?.semantic_ready) return null;
  return (
    <button
      type="button"
      role="menuitem"
      className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:bg-popover-hover focus-visible:outline-none"
      onClick={() => {
        onSelect();
        router.push(`/search?model=${modelId}`);
      }}
    >
      <Search className="h-4 w-4" />
      {t("aiSearch.relatedModels")}
    </button>
  );
}
