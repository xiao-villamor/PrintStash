"use client";

import { useEffect, useState } from "react";
import { CategoryRead, TagRead } from "@/types";
import {
  listCategories,
  listTags,
  createCategory,
  createTag,
  deleteCategory,
  deleteTag,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Plus, X, FolderTree, Tag as TagIcon } from "lucide-react";

export function TaxonomyManager() {
  const auth = useRequireAuth();
  const [categories, setCategories] = useState<CategoryRead[]>([]);
  const [tags, setTags] = useState<TagRead[]>([]);
  const [newCat, setNewCat] = useState("");
  const [newTag, setNewTag] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      const [c, t] = await Promise.all([listCategories(), listTags()]);
      setCategories(c);
      setTags(t);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function handleCreateCategory() {
    const name = newCat.trim();
    if (!name) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await createCategory({ name });
      setNewCat("");
      toast.success(`Category "${name}" created`);
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleDeleteCategory(id: number) {
    try {
      await deleteCategory(id);
      toast.success("Category removed");
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

  const assignedCategoryModels = categories.reduce((sum, category) => sum + category.model_count, 0);
  const assignedTagModels = tags.reduce((sum, tag) => sum + tag.model_count, 0);
  const topCategories = [...categories]
    .sort((a, b) => b.model_count - a.model_count || a.name.localeCompare(b.name))
    .slice(0, 4);
  const topTags = [...tags]
    .sort((a, b) => b.model_count - a.model_count || a.name.localeCompare(b.name))
    .slice(0, 8);

  return (
    <div className="space-y-4 sm:space-y-6 lg:space-y-8">
      {error && (
        <div className="rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-3 text-xs text-[var(--error)] font-mono">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden divide-x-0 lg:divide-x divide-y lg:divide-y-0 divide-[var(--surface-variant)]">
        {[
          { label: "Categories", value: categories.length, detail: `${assignedCategoryModels} assigned` },
          { label: "Tags", value: tags.length, detail: `${assignedTagModels} assignments` },
          { label: "Top category", value: topCategories[0]?.name ?? "None", detail: `${topCategories[0]?.model_count ?? 0} models` },
          { label: "Top tag", value: topTags[0]?.name ?? "None", detail: `${topTags[0]?.model_count ?? 0} models` },
        ].map((item) => (
          <div key={item.label} className="p-4 sm:p-5 min-w-0">
            <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--on-surface-variant)]">
              {item.label}
            </p>
            <p className="mt-2 text-xl font-semibold text-[var(--on-surface)] truncate">
              {item.value}
            </p>
            <p className="mt-1 text-xs text-[var(--on-surface-variant)]">
              {item.detail}
            </p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)] gap-4 sm:gap-6 lg:gap-8 items-start">
        <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-[var(--outline-variant)] flex flex-col sm:flex-row sm:items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <FolderTree className="h-4 w-4 text-[var(--on-surface-variant)] flex-shrink-0" />
            <h3 className="text-sm font-semibold text-[var(--on-surface)]">
              Categories
            </h3>
            <span className="font-mono text-xs text-[var(--on-surface-variant)]">
              ({categories.length})
            </span>
          </div>
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateCategory(); }}
            className="flex items-center gap-2"
          >
            <input
              value={newCat}
              onChange={(e) => setNewCat(e.target.value)}
              disabled={!auth.isAuthenticated}
              placeholder={auth.isAuthenticated ? "New category..." : "Sign in to add"}
              className="flex-1 sm:flex-none sm:w-40 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!newCat.trim() || !auth.isAuthenticated}
              className="p-1.5 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity disabled:opacity-50 flex-shrink-0"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>

        <div className="p-3 sm:p-4">
          {loading ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
          ) : categories.length === 0 ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">
              No categories yet. Create one above.
            </p>
          ) : (
            <div className="space-y-1">
              {categories.map((c) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-[var(--surface-container-low)] group gap-2"
                >
                  <div className="flex items-center gap-2 min-w-0 overflow-hidden">
                    <span className="text-sm text-[var(--on-surface)] truncate">
                      {c.name}
                    </span>
                    <span className="font-mono text-[10px] text-[var(--on-surface-variant)] truncate hidden sm:inline">
                      {c.path}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs text-[var(--on-surface-variant)]">
                      {c.model_count} models
                    </span>
                    <button
                      onClick={() => {
                        if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                        if (c.model_count > 0) {
                          toast.warning("Cannot delete category", "Remove all assigned models first.");
                          return;
                        }
                        handleDeleteCategory(c.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-[var(--error-container)]/30 text-[var(--error)] disabled:opacity-20"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-[var(--outline-variant)] flex flex-col sm:flex-row sm:items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <TagIcon className="h-4 w-4 text-[var(--on-surface-variant)] flex-shrink-0" />
            <h3 className="text-sm font-semibold text-[var(--on-surface)]">
              Tags
            </h3>
            <span className="font-mono text-xs text-[var(--on-surface-variant)]">
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
              className="flex-1 sm:flex-none sm:w-40 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!newTag.trim() || !auth.isAuthenticated}
              className="p-1.5 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity disabled:opacity-50 flex-shrink-0"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>

        <div className="p-3 sm:p-4">
          {loading ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
          ) : tags.length === 0 ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">
              No tags yet. Create one above.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {tags.map((t) => (
                <span
                  key={t.id}
                  className="inline-flex items-center gap-1.5 bg-[var(--surface-container)] text-[var(--on-surface)] px-2.5 py-1.5 rounded font-mono text-xs uppercase tracking-wider group"
                >
                  {t.name}
                  <span className="text-[10px] text-[var(--on-surface-variant)]">
                    ({t.model_count})
                  </span>
                  <button
                    onClick={() => {
                      if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                      handleDeleteTag(t.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:text-[var(--error)]"
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
        <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
          <div className="px-4 sm:px-6 py-3 border-b border-[var(--outline-variant)]">
            <h3 className="text-sm font-semibold text-[var(--on-surface)]">Most used categories</h3>
          </div>
          <div className="p-3 sm:p-4 space-y-2">
            {loading ? (
              <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
            ) : topCategories.length === 0 ? (
              <p className="text-xs text-[var(--on-surface-variant)] font-mono">No category usage yet.</p>
            ) : (
              topCategories.map((category) => (
                <div key={category.id} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="text-sm text-[var(--on-surface)] truncate">{category.path}</span>
                  <span className="font-mono text-xs text-[var(--on-surface-variant)]">{category.model_count}</span>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
          <div className="px-4 sm:px-6 py-3 border-b border-[var(--outline-variant)]">
            <h3 className="text-sm font-semibold text-[var(--on-surface)]">Most used tags</h3>
          </div>
          <div className="p-3 sm:p-4">
            {loading ? (
              <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
            ) : topTags.length === 0 ? (
              <p className="text-xs text-[var(--on-surface-variant)] font-mono">No tag usage yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {topTags.map((tag) => (
                  <span
                    key={tag.id}
                    className="inline-flex items-center gap-1.5 bg-[var(--surface-container)] text-[var(--on-surface)] px-2.5 py-1.5 rounded font-mono text-xs uppercase tracking-wider"
                  >
                    {tag.name}
                    <span className="text-[10px] text-[var(--on-surface-variant)]">
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
