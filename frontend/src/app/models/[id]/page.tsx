import { notFound } from "next/navigation";
import { getModel } from "@/lib/api";
import { ModelDetail } from "@/components/model-detail";

export const revalidate = 0;

export default async function ModelPage({
  params,
}: {
  params: { id: string };
}) {
  const id = parseInt(params.id, 10);
  if (isNaN(id)) {
    notFound();
  }

  let model;
  try {
    model = await getModel(id);
  } catch (err) {
    const status =
      typeof err === "object" && err !== null && "status" in err
        ? err.status
        : null;
    if (status === 404) {
      notFound();
    }
    throw err;
  }

  return <ModelDetail model={model} />;
}
