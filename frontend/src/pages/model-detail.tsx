import { useParams } from "react-router-dom";

import { ModelDetailClientView } from "@/components/model-detail/client-view";
import NotFound from "./not-found";

export default function ModelDetailPage() {
  const { id } = useParams();
  const modelId = Number(id);
  if (!id || Number.isNaN(modelId)) return <NotFound />;
  // The client view owns the authorized read for this route identity.
  return <ModelDetailClientView key={modelId} id={modelId} initialModel={null} />;
}
