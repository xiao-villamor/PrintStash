/**
 * The collection tree endpoints, answered from a flat list of collections.
 *
 * Components read the tree a level, a lookup or a search page at a time
 * (`/collections/children`, `/lookup`, `/search`). A test describes the library
 * once, as the flat list it always has, and these routes answer each read the
 * way the server does: top level = collections whose parent is not in the list,
 * children and search results sorted by name without regard to case, and each
 * node carrying its child count, descendant count and display path. `model_count`
 * is passed through as given, so a test states the subtree totals it expects.
 */

import type {
  CollectionLookupRead,
  CollectionNodeRead,
  CollectionPage,
  CollectionRead,
  CollectionRole,
} from "@/types";
import { json, type RouteTable } from "@/test-support/render";

const ROLE_RANK = { view: 1, edit: 2, admin: 3 } satisfies Record<CollectionRole, number>;

function byName(a: CollectionRead, b: CollectionRead): number {
  const left = a.name.toLowerCase();
  const right = b.name.toLowerCase();
  return left === right ? a.id - b.id : left < right ? -1 : 1;
}

/** Read the collection tree the way the server serves it, from a flat list. */
export function collectionTreeRoutes(collections: CollectionRead[]): RouteTable {
  const byId = new Map(collections.map((collection) => [collection.id, collection]));

  function parentOf(collection: CollectionRead): CollectionRead | undefined {
    return collection.parent_id === null ? undefined : byId.get(collection.parent_id);
  }

  function node(collection: CollectionRead): CollectionNodeRead {
    const chain: CollectionRead[] = [];
    for (let at: CollectionRead | undefined = collection; at; at = parentOf(at)) chain.unshift(at);
    const below = collections.filter((other) => other.path.startsWith(`${collection.path}/`));
    return {
      ...collection,
      child_count: collections.filter((other) => other.parent_id === collection.id).length,
      descendant_count: below.length,
      display_path: chain.map((at) => at.name).join("/"),
    };
  }

  function page(items: CollectionRead[]): CollectionPage {
    return { items: [...items].sort(byName).map(node), next_cursor: null };
  }

  return {
    "GET /api/v1/collections/children": (url) => {
      const parent = new URL(url, "http://test").searchParams.get("parent_id");
      if (parent === null) return json(page(collections.filter((c) => !parentOf(c))));
      if (!byId.has(Number(parent))) return json({ detail: "collection_not_found" }, 404);
      return json(page(collections.filter((c) => c.parent_id === Number(parent))));
    },
    "GET /api/v1/collections/lookup": (url) => {
      const params = new URL(url, "http://test").searchParams;
      const path = params.get("path");
      const id = params.get("id");
      const found = collections.find((collection) =>
        id !== null ? collection.id === Number(id) : collection.path === path,
      );
      if (found === undefined) return json({ detail: "collection_not_found" }, 404);
      const ancestors: CollectionRead[] = [];
      for (let at = parentOf(found); at; at = parentOf(at)) ancestors.unshift(at);
      const body: CollectionLookupRead = {
        collection: node(found),
        ancestors: ancestors.map(node),
      };
      return json(body);
    },
    "GET /api/v1/collections/search": (url) => {
      const params = new URL(url, "http://test").searchParams;
      const query = (params.get("q") ?? "").trim().toLowerCase();
      const role = params.get("min_role");
      const minimum = role === "admin" || role === "edit" ? ROLE_RANK[role] : ROLE_RANK.view;
      return json(
        page(
          collections.filter(
            (collection) =>
              collection.name.toLowerCase().includes(query) &&
              (collection.effective_role === null
                ? false
                : ROLE_RANK[collection.effective_role] >= minimum),
          ),
        ),
      );
    },
  };
}
