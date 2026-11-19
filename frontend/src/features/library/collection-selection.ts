import type { CollectionNodeRead, ModelListItem } from "@/types";

export type ModelSelection =
  | { kind: "none" }
  | { kind: "explicit" }
  | { kind: "implied"; collections: CollectionNodeRead[] };

export function modelSelection(
  model: ModelListItem,
  explicit: ReadonlySet<number>,
  collections: Iterable<CollectionNodeRead>,
): ModelSelection {
  const covering = Array.from(collections).filter(
    ({ id, path }) => model.collection_id === id || model.collection?.startsWith(`${path}/`),
  );
  if (covering.length > 0) return { kind: "implied", collections: covering };
  return explicit.has(model.id) ? { kind: "explicit" } : { kind: "none" };
}
