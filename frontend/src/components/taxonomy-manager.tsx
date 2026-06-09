"use client";

import { useEffect, useMemo, useState } from "react";
import { CollectionRead, TagRead } from "@/types";
import {
  listCollections,
  listTags,
  createCollection,
  createTag,
  deleteCollection,
  deleteTag,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  FolderTree,
  Plus,
  Tag as TagIcon,
  X,
} from "lucide-react";

interface CollectionNode {
  collection: CollectionRead;
  children: CollectionNode[];
}

function buildCollectionTree(collections: CollectionRead[]): CollectionNode[] {
  const byId = new Map<number, CollectionNode>();
  for (const collection of collections) {
    byId.set(collection.id, { collection, children: [] });
  }
  const roots: CollectionNode[] = [];
  for (const node of byId.values()) {
    const parent =
      node.collection.parent_id == null
        ? null
        : byId.get(node.collection.parent_id);
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  const sortNodes = (nodes: CollectionNode[]) => {
    nodes.sort((a, b) => a.collection.name.localeCompare(b.collection.name));
    nodes.forEach((node) => sortNodes(node.children));
  };
  sortNodes(roots);
  return roots;
}

function CollectionTreeRow({
  node,
  depth,
  auth,
  expanded,
  onToggle,
  onAddChild,
  onDelete,
}: {
  node: CollectionNode;
  depth: number;
  auth: ReturnType<typeof useRequireAuth>;
  expanded: Set<number>;
  onToggle: (id: number) => void;
  onAddChild: (collection: CollectionRead) => void;
  onDelete: (collection: CollectionRead) => void;
}) {
  const collection = node.collection;
  const hasChildren = node.children.length > 0;
  const isOpen = expanded.has(collection.id);
  return (
    <>
      <div
        className="flex items-center justify-between py-1.5 pr-2 rounded hover:bg-muted/50 group gap-2"
        style={{ paddingLeft: `${depth * 18 + 8}px` }}
      >
        <div className="flex items-center gap-2 min-w-0 overflow-hidden">
          {hasChildren ? (
            <button
              type="button"
              onClick={() => onToggle(collection.id)}
              className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={isOpen ? "Collapse collection" : "Expand collection"}
            >
              <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-90" : ""}`} />
            </button>
          ) : (
            <span className="w-4 flex-shrink-0" />
          )}
          {isOpen ? (
            <FolderOpen className="h-4 w-4 flex-shrink-0 text-blue-600 dark:text-orange-500" />
          ) : (
            <Folder className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          )}
          <span className="text-sm text-foreground truncate">
            {collection.name}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground truncate hidden sm:inline">
            {collection.path}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {collection.model_count} models
          </span>
          <button
            type="button"
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              onAddChild(collection);
            }}
            className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-muted text-muted-foreground"
            title={`Add subcollection to ${collection.name}`}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              onDelete(collection);
            }}
            className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-50/30 text-red-600"
            title={`Delete ${collection.name}`}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {isOpen && node.children.map((child) => (
        <CollectionTreeRow
          key={child.collection.id}
          node={child}
          depth={depth + 1}
          auth={auth}
          expanded={expanded}
          onToggle={onToggle}
          onAddChild={onAddChild}
          onDelete={onDelete}
        />
      ))}
    </>
  );
}

export function TaxonomyManager() {
  const auth = useRequireAuth();
  const [collections, setCollections] = useState<CollectionRead[]>([]);
  const [tags, setTags] = useState<TagRead[]>([]);
  const [newCollection, setNewCollection] = useState("");
  const [parentCollectionId, setParentCollectionId] = useState<number | null>(null);
  const [newTag, setNewTag] = useState("");
  const [expandedCollections, setExpandedCollections] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      const [c, t] = await Promise.all([listCollections(), listTags()]);
      setCollections(c);
      setTags(t);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function handleCreateCollection() {
    const name = newCollection.trim();
    if (!name) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await createCollection({ name, parent_id: parentCollectionId });
      setNewCollection("");
      toast.success(`Collection "${name}" created`);
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleDeleteCollection(id: number) {
    try {
      await deleteCollection(id);
      toast.success("Collection removed");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleCreateTag() {
    const name = newTag.trim();
    if (!name) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await createTag({ name });
      setNewTag("");
      toast.success(`Tag "${name}" created`);
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleDeleteTag(id: number) {
    try {
      await deleteTag(id);
      toast.success("Tag removed");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  const tree = useMemo(() => buildCollectionTree(collections), [collections]);
  const assignedCollectionModels = collections.reduce((sum, collection) => sum + collection.model_count, 0);
  const assignedTagModels = tags.reduce((sum, tag) => sum + tag.model_count, 0);
  const topCollections = [...collections]
    .sort((a, b) => b.model_count - a.model_count || a.name.localeCompare(b.name))
    .slice(0, 4);
  const topTags = [...tags]
    .sort((a, b) => b.model_count - a.model_count || a.name.localeCompare(b.name))
    .slice(0, 8);

  function toggleCollection(id: number) {
    setExpandedCollections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-4 sm:space-y-6 lg:space-y-8">
      {error && (
        <div className="rounded border border-red-300/30 bg-red-50/20 p-3 text-xs text-red-600">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 bg-card border border-border rounded overflow-hidden divide-x-0 lg:divide-x divide-y lg:divide-y-0 divide-slate-100">
        {[
          { label: "Collections", value: collections.length, detail: `${assignedCollectionModels} assigned` },
          { label: "Tags", value: tags.length, detail: `${assignedTagModels} assignments` },
          { label: "Top collection", value: topCollections[0]?.name ?? "None", detail: `${topCollections[0]?.model_count ?? 0} models` },
          { label: "Top tag", value: topTags[0]?.name ?? "None", detail: `${topTags[0]?.model_count ?? 0} models` },
        ].map((item) => (
          <div key={item.label} className="p-4 sm:p-5 min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
              {item.label}
            </p>
            <p className="mt-2 text-xl font-semibold text-foreground truncate">
              {item.value}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {item.detail}
            </p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)] gap-4 sm:gap-6 lg:gap-8 items-start">
        <div className="bg-card border border-border rounded overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-border flex flex-col sm:flex-row sm:items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <FolderTree className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            <h3 className="text-sm font-semibold text-foreground">
              Collections
            </h3>
            <span className="text-xs text-muted-foreground">
              ({collections.length})
            </span>
          </div>
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateCollection(); }}
            className="flex items-center gap-2"
          >
            <input
              value={newCollection}
              onChange={(e) => setNewCollection(e.target.value)}
              disabled={!auth.isAuthenticated}
              placeholder={auth.isAuthenticated ? "New collection..." : "Sign in to add"}
              className="flex-1 sm:flex-none sm:w-40 bg-background text-foreground text-xs border border-border rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent disabled:opacity-50"
            />
            <select
              value={parentCollectionId ?? ""}
              onChange={(e) => setParentCollectionId(e.target.value ? Number(e.target.value) : null)}
              disabled={!auth.isAuthenticated}
              className="max-w-44 bg-background text-foreground text-xs border border-border rounded px-2 py-[6px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent disabled:opacity-50"
              title="Parent collection"
            >
              <option value="">Root</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.path}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={!newCollection.trim() || !auth.isAuthenticated}
              className="p-1.5 rounded bg-blue-600 dark:bg-orange-600 text-white hover:opacity-90 transition-opacity disabled:opacity-50 flex-shrink-0"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>

        <div className="p-3 sm:p-4">
          {loading ? (
            <p className="text-xs text-muted-foreground">Loading...</p>
          ) : collections.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No collections yet. Create one above.
            </p>
          ) : (
            <div className="space-y-1">
              {tree.map((node) => (
                <CollectionTreeRow
                  key={node.collection.id}
                  node={node}
                  depth={0}
                  auth={auth}
                  expanded={expandedCollections}
                  onToggle={toggleCollection}
                  onAddChild={(collection) => {
                    setParentCollectionId(collection.id);
                    setNewCollection("");
                    setExpandedCollections((prev) => new Set(prev).add(collection.id));
                  }}
                  onDelete={(collection) => {
                    if (collection.model_count > 0) {
                      toast.warning("Cannot delete collection", "Remove all assigned models first.");
                      return;
                    }
                    handleDeleteCollection(collection.id);
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="bg-card border border-border rounded overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-border flex flex-col sm:flex-row sm:items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <TagIcon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            <h3 className="text-sm font-semibold text-foreground">
              Tags
            </h3>
            <span className="text-xs text-muted-foreground">
              ({tags.length})
            </span>
          </div>
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateTag(); }}
            className="flex items-center gap-2"
          >
            <input
              value={newTag}
              onChange={(e) => setNewTag(e.target.value)}
              disabled={!auth.isAuthenticated}
              placeholder={auth.isAuthenticated ? "New tag..." : "Sign in to add"}
              className="flex-1 sm:flex-none sm:w-40 bg-background text-foreground text-xs border border-border rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!newTag.trim() || !auth.isAuthenticated}
              className="p-1.5 rounded bg-blue-600 dark:bg-orange-600 text-white hover:opacity-90 transition-opacity disabled:opacity-50 flex-shrink-0"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>

        <div className="p-3 sm:p-4">
          {loading ? (
            <p className="text-xs text-muted-foreground">Loading...</p>
          ) : tags.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No tags yet. Create one above.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {tags.map((t) => (
                <span
                  key={t.id}
                  className="inline-flex items-center gap-1.5 bg-muted text-foreground px-2.5 py-1.5 rounded text-xs uppercase tracking-wider group"
                >
                  {t.name}
                  <span className="text-[10px] text-muted-foreground">
                    ({t.model_count})
                  </span>
                  <button
                    onClick={() => {
                      if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                      handleDeleteTag(t.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:text-red-600"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 lg:gap-8">
        <div className="bg-card border border-border rounded overflow-hidden">
          <div className="px-4 sm:px-6 py-3 border-b border-border">
            <h3 className="text-sm font-semibold text-foreground">Most used collections</h3>
          </div>
          <div className="p-3 sm:p-4 space-y-2">
            {loading ? (
              <p className="text-xs text-muted-foreground">Loading...</p>
            ) : topCollections.length === 0 ? (
              <p className="text-xs text-muted-foreground">No collection usage yet.</p>
            ) : (
              topCollections.map((collection) => (
                <div key={collection.id} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="text-sm text-foreground truncate">{collection.path}</span>
                  <span className="text-xs text-muted-foreground">{collection.model_count}</span>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="bg-card border border-border rounded overflow-hidden">
          <div className="px-4 sm:px-6 py-3 border-b border-border">
            <h3 className="text-sm font-semibold text-foreground">Most used tags</h3>
          </div>
          <div className="p-3 sm:p-4">
            {loading ? (
              <p className="text-xs text-muted-foreground">Loading...</p>
            ) : topTags.length === 0 ? (
              <p className="text-xs text-muted-foreground">No tag usage yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {topTags.map((tag) => (
                  <span
                    key={tag.id}
                    className="inline-flex items-center gap-1.5 bg-muted text-foreground px-2.5 py-1.5 rounded text-xs uppercase tracking-wider"
                  >
                    {tag.name}
                    <span className="text-[10px] text-muted-foreground">
                      {tag.model_count}
                    </span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
