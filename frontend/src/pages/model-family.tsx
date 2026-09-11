import { useParams } from "react-router-dom";
import { FamilyDetail } from "@/components/families/detail";
import NotFound from "./not-found";

export default function ModelFamilyPage() {
  const { id } = useParams();
  const familyId = Number(id);
  if (!Number.isSafeInteger(familyId) || familyId <= 0) return <NotFound />;
  return <FamilyDetail key={familyId} id={familyId} />;
}
