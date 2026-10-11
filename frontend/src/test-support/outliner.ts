/** Sidebar API fixtures: tests describe visible rows, never pass leaves as component props. */
import type { CollectionRead, MultipartModelListItem, OutlinerModelRead } from "@/types";
import type {
  OutlinerCollection,
  OutlinerEntry,
  OutlinerMatch,
  OutlinerRestoreParams,
  OutlinerRestoreRead,
} from "@/types/outliner";
import { modelListSearch } from "@/lib/api/models";
import { aCollectionNode } from "./factories";
import { json, type RouteTable } from "./render";

export function outlinerRoutes(
  collections: CollectionRead[],
  models: OutlinerModelRead[] = [],
  multipart: MultipartModelListItem[] = [],
): RouteTable {
  function leaves(params: URLSearchParams, searching = false): OutlinerEntry[] {
    const view = params.get("view");
    const members = new Set(multipart.flatMap((set) => set.member_model_ids));
    const singles: OutlinerEntry[] = models
      .filter(
        (model) =>
          view !== "multipart" &&
          (view !== "components" || members.has(model.id)) &&
          (view !== "organized" || searching || !members.has(model.id)),
      )
      .map((model) => ({ ...model, kind: "model" }));
    const sets: OutlinerEntry[] =
      view === "components"
        ? []
        : multipart.map((set) => ({
            id: set.id,
            edit_epoch: set.edit_epoch,
            edit_version: set.edit_version,
            name: set.name,
            collection: set.collection,
            collection_id: set.collection_id,
            collection_label: set.collection_label,
            kind: "multipart",
          }));
    return [...singles, ...sets];
  }
  function label(collection: CollectionRead): string {
    const ancestors = collections
      .filter((parent) => collection.path.startsWith(`${parent.path}/`))
      .sort((a, b) => a.path.length - b.path.length);
    return [...ancestors, collection].map((node) => node.name).join("/");
  }
  function node(collection: CollectionRead, params: URLSearchParams): OutlinerCollection {
    const entries = leaves(params);
    const below = entries.filter(
      (entry) =>
        entry.collection === collection.path || entry.collection?.startsWith(`${collection.path}/`),
    );
    const filtered =
      params.has("tag") ||
      params.has("printer_id") ||
      params.has("printer_presence") ||
      params.has("favorites");
    const children = collections.filter((child) => child.parent_id === collection.id);
    const view = params.get("view");
    const count =
      filtered || view === "multipart" || view === "components"
        ? below.length
        : Math.max(collection.model_count, below.filter((entry) => entry.kind === "model").length) +
          below.filter((entry) => entry.kind === "multipart").length;
    return {
      ...aCollectionNode({
        ...collection,
        child_count: children.length,
        descendant_count: collections.filter((child) =>
          child.path.startsWith(`${collection.path}/`),
        ).length,
        display_path: label(collection),
      }),
      direct_entry_count: entries.filter((entry) => entry.collection === collection.path).length,
      subtree_entry_count: count,
      visible_child_count: children.length,
    };
  }
  function page<T extends { name: string; id: number }>(rows: T[], params: URLSearchParams) {
    const sorted = [...rows].sort(
      (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.id - b.id,
    );
    const offset = Number(params.get("cursor") ?? 0);
    return {
      items: sorted.slice(offset, offset + 50),
      next_cursor: sorted.length > offset + 50 ? String(offset + 50) : null,
    };
  }
  function collectionPage(params: URLSearchParams) {
    const parent = params.get("parent_id");
    const visible = collections
      .filter((item) =>
        parent === null
          ? !collections.some((p) => p.id === item.parent_id)
          : item.parent_id === Number(parent),
      )
      .map((item) => node(item, params));
    const filtered =
      params.has("tag") ||
      params.has("printer_id") ||
      params.has("printer_presence") ||
      params.has("favorites");
    return {
      ...page(
        visible.filter((item) => !filtered || item.subtree_entry_count > 0),
        params,
      ),
      parent_direct_entry_count: leaves(params).filter(
        (entry) => entry.collection_id === (parent === null ? null : Number(parent)),
      ).length,
      revealed: visible.find((item) => item.id === Number(params.get("reveal_id"))) ?? null,
    };
  }
  function entryPage(params: URLSearchParams) {
    const folder = params.get("collection_id");
    return page(
      leaves(params).filter(
        (entry) => entry.collection_id === (folder === null ? null : Number(folder)),
      ),
      params,
    );
  }
  return {
    "POST /api/v1/outliner/restore": (_url, init) => {
      const body: OutlinerRestoreParams = JSON.parse(String(init?.body));
      const parents = [
        null,
        ...collections.filter((node) => body.expanded_paths.includes(node.path)),
      ];
      const levels = parents.map((parent) => {
        const params = modelListSearch(body);
        params.set("view", body.view);
        if (parent) {
          params.set("parent_id", String(parent.id));
          params.set("collection_id", String(parent.id));
        }
        const reveal = collections.find(
          (node) =>
            node.parent_id === (parent?.id ?? null) &&
            (node.path === body.selected_path || body.selected_path?.startsWith(`${node.path}/`)),
        );
        if (reveal) params.set("reveal_id", String(reveal.id));
        return { id: parent?.id ?? null, params };
      });
      return json({
        collections: levels.map(({ id, params }) => ({
          parent_id: id,
          page: collectionPage(params),
        })),
        entries: levels.map(({ id, params }) => ({ collection_id: id, page: entryPage(params) })),
      } satisfies OutlinerRestoreRead);
    },
    "GET /api/v1/outliner/collections": (url) =>
      json(collectionPage(new URL(url, "http://test").searchParams)),
    "GET /api/v1/outliner/entries": (url) => {
      const params = new URL(url, "http://test").searchParams;
      return json(entryPage(params));
    },
    "GET /api/v1/outliner/search": (url) => {
      const params = new URL(url, "http://test").searchParams;
      const q = (params.get("q") ?? "").toLowerCase();
      const folders: OutlinerMatch[] = collections.map((item) => ({
        kind: "collection",
        id: item.id,
        name: item.name,
        collection_id: item.id,
        collection: item.path,
        collection_label: label(item),
      }));
      return json(
        page(
          [...folders, ...leaves(params, true)].filter((entry) =>
            entry.name.toLowerCase().includes(q),
          ),
          params,
        ),
      );
    },
  };
}
