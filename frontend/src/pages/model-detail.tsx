import { useParams } from "react-router-dom";

import { ModelDetailClientView } from "@/components/model-detail/client-view";
import NotFound from "./not-found";

export default function ModelDetailPage() {
  const { id } = useParams();
  const modelId = Number(id);
  if (!id || Number.isNaN(modelId)) return <NotFound />;
  // No SSR prefetch: the client view fetches with the stored token.
  return <ModelDetailClientView id={modelId} initialModel={null} />;
}
