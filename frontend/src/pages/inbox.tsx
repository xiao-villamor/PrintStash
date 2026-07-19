import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, Download, ExternalLink, Inbox, RefreshCw, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  capturePendingImport,
  batchPendingImports,
  dismissPendingImport,
  importPendingImport,
  listPendingImports,
  retryPendingImport,
  updatePendingImport,
} from "@/lib/api";
import { useCollections } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { Link } from "@/lib/navigation";
import type { InboxItem } from "@/types";

const ACTIVE = new Set(["captured", "resolving", "importing"]);

function choices(item: InboxItem) {
  if (item.manifest.kind === "archive") return item.manifest.entries ?? [];
  if (item.manifest.kind === "model_files") return item.manifest.files ?? [];
  if (item.manifest.kind === "collection") return item.manifest.members ?? [];
  return [];
}

export default function InboxPage() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [collectionId, setCollectionId] = useState<number | null>(null);
  const [tags, setTags] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [selected, setSelected] = useState<Record<number, string[]>>({});
  const [bulkSelected, setBulkSelected] = useState<Set<number>>(new Set());
  const [bulkTags, setBulkTags] = useState("");
  const collections = useCollections().data ?? [];

  const refresh = useCallback(async () => {
    try { setItems(await listPendingImports(true)); }
    catch (error) { toast.error(error); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!items.some((item) => ACTIVE.has(item.state))) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [items, refresh]);

  const pendingCount = useMemo(
    () => items.filter((item) => !["completed", "dismissed"].includes(item.state)).length,
    [items],
  );

  async function capture(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim()) return;
    setCapturing(true);
    try {
      await capturePendingImport({
        url: url.trim(),
        title: title.trim() || undefined,
        collection_id: collectionId,
        tags: tags.split(",").map((item) => item.trim()).filter(Boolean),
      });
      setUrl(""); setTitle(""); setTags("");
      await refresh();
      toast.success("Added to Pending Imports");
    } catch (error) { toast.error(error); }
    finally { setCapturing(false); }
  }

  function toggleChoice(itemId: number, id: string) {
    setSelected((current) => {
      const values = current[itemId] ?? [];
      return { ...current, [itemId]: values.includes(id) ? values.filter((value) => value !== id) : [...values, id] };
    });
  }

  async function action(task: () => Promise<unknown>, success?: string) {
    try { await task(); if (success) toast.success(success); await refresh(); }
    catch (error) { toast.error(error); }
  }

  function toggleBulk(itemId: number) {
    setBulkSelected((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId); else next.add(itemId);
      return next;
    });
  }

  async function bulkAction(actionName: "retry" | "import" | "dismiss" | "set_collection" | "add_tags") {
    if (bulkSelected.size === 0) return;
    const tagValues = bulkTags.split(",").map((item) => item.trim()).filter(Boolean);
    if (actionName === "add_tags" && tagValues.length === 0) {
      toast.error("Enter at least one tag to add");
      return;
    }
    await action(
      () => batchPendingImports({
        item_ids: [...bulkSelected],
        action: actionName,
        ...(actionName === "set_collection" ? { collection_id: collectionId } : {}),
        ...(actionName === "add_tags" ? { tags: tagValues } : {}),
      }),
      `${bulkSelected.size} item${bulkSelected.size === 1 ? "" : "s"} updated`,
    );
    setBulkSelected(new Set());
    if (actionName === "add_tags") setBulkTags("");
  }

  return (
    <PageContainer>
      <PageHeader
        title="Pending Imports"
        description={`${pendingCount} capture${pendingCount === 1 ? "" : "s"} waiting for review or import.`}
      />

      <Card className="mb-5">
        <CardContent className="pt-6">
          <form onSubmit={capture} className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
            <Input type="url" required placeholder="https://printables.com/model/..." value={url} onChange={(event) => setUrl(event.target.value)} aria-label="Model URL" />
            <Input placeholder="Optional title" value={title} onChange={(event) => setTitle(event.target.value)} aria-label="Display title" />
            <select value={collectionId ?? ""} onChange={(event) => setCollectionId(event.target.value ? Number(event.target.value) : null)} className="h-10 rounded-md border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" aria-label="Target Collection">
              <option value="">Choose during review</option>
              {collections.map((collection) => <option key={collection.id} value={collection.id}>{collection.path}</option>)}
            </select>
            <Input placeholder="tags, comma separated" value={tags} onChange={(event) => setTags(event.target.value)} aria-label="Tags" />
            <Button type="submit" loading={capturing}><Download className="h-4 w-4" /> Capture</Button>
          </form>
        </CardContent>
      </Card>

      {loading ? <p className="text-sm text-muted-foreground">Loading Pending Imports…</p> : items.length === 0 ? (
        <EmptyState icon={Inbox} title="Capture now, organize later" description="Paste a supported model page or direct file URL. PrintStash will resolve it safely and keep review choices across restarts." />
      ) : (
        <div className="space-y-3">
          {bulkSelected.size > 0 && (
            <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-md border border-border bg-card p-3 shadow-sm">
              <span className="mr-auto text-sm font-medium">{bulkSelected.size} selected</span>
              <Button size="xs" variant="outline" onClick={() => void bulkAction("set_collection")}>Set collection</Button>
              <Input value={bulkTags} onChange={(event) => setBulkTags(event.target.value)} placeholder="tags, comma separated" aria-label="Tags to add" className="h-8 w-44 text-xs" />
              <Button size="xs" variant="outline" onClick={() => void bulkAction("add_tags")}>Add tags</Button>
              <Button size="xs" variant="outline" onClick={() => void bulkAction("retry")}>Retry eligible</Button>
              <Button size="xs" onClick={() => void bulkAction("import")}>Import ready</Button>
              <Button size="xs" variant="ghost" onClick={() => void bulkAction("dismiss")}>Dismiss</Button>
            </div>
          )}
          {items.filter((item) => item.state !== "dismissed").map((item) => {
            const itemChoices = choices(item);
            const chosen = selected[item.id] ?? item.manifest.selected_ids ?? [];
            return (
              <Card key={item.id} className="animate-card-in">
                <CardContent className="space-y-3 pt-6">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
                    <Checkbox checked={bulkSelected.has(item.id)} onChange={() => toggleBulk(item.id)} aria-label={`Select ${item.display_title || item.source_hostname || "pending import"}`} />
                    <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                      {item.state === "completed" ? <CheckCircle2 className="h-4 w-4 text-success" /> : <Clock3 className="h-4 w-4" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <h2 className="truncate text-sm font-semibold">{item.display_title || item.source_hostname || "Pending Import"}</h2>
                      <a href={item.source_url ?? "#"} target="_blank" rel="noreferrer" className="flex items-center gap-1 truncate text-xs text-muted-foreground hover:text-foreground">{item.source_hostname}<ExternalLink className="h-3 w-3" /></a>
                    </div>
                    <Badge variant={item.state === "completed" ? "success" : item.state === "failed" ? "destructive" : item.state === "review" ? "warning" : "secondary"}>{item.state}</Badge>
                  </div>

                  {item.error_code && <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{item.error_code.replaceAll("_", " ")}</p>}

                  {!["importing", "completed", "dismissed"].includes(item.state) && (
                    <div className="grid gap-2 sm:grid-cols-2">
                      <select value={item.target_collection_id ?? ""} onChange={(event) => void action(() => updatePendingImport(item.id, { collection_id: event.target.value ? Number(event.target.value) : null }))} className="h-9 rounded-md border border-input bg-background px-3 text-xs text-foreground" aria-label="Target Collection">
                        <option value="">No collection selected</option>
                        {collections.map((collection) => <option key={collection.id} value={collection.id}>{collection.path}</option>)}
                      </select>
                      <Input defaultValue={item.requested_tags.join(", ")} onBlur={(event) => void action(() => updatePendingImport(item.id, { tags: event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) }))} placeholder="tags, comma separated" aria-label="Import tags" />
                    </div>
                  )}

                  {itemChoices.length > 0 && item.state === "review" && (
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                      {itemChoices.map((choice) => (
                        <label key={choice.id} className="flex cursor-pointer items-center gap-2 rounded-md border border-border p-2 text-xs hover:bg-muted">
                          <Checkbox checked={chosen.includes(choice.id)} onChange={() => toggleChoice(item.id, choice.id)} />
                          <span className="min-w-0 flex-1 truncate">{"name" in choice ? choice.name : choice.title}</span>
                        </label>
                      ))}
                    </div>
                  )}

                  <div className="flex flex-wrap justify-end gap-2">
                    {item.state === "review" && (
                      <Button size="xs" onClick={() => void action(async () => {
                        await updatePendingImport(item.id, { selected_ids: chosen });
                        await importPendingImport(item.id, chosen);
                      }, "Import started")}>Import</Button>
                    )}
                    {item.state === "failed" && item.retryable && <Button size="xs" variant="outline" onClick={() => void action(() => retryPendingImport(item.id), "Retry queued")}><RefreshCw className="h-3.5 w-3.5" /> Retry</Button>}
                    {item.state === "completed" && item.resulting_model_id && <Button size="xs" variant="outline" asChild><Link href={`/models/${item.resulting_model_id}`}>Open Model</Link></Button>}
                    {item.state !== "importing" && <Button size="xs" variant="ghost" onClick={() => void action(() => dismissPendingImport(item.id))}><Trash2 className="h-3.5 w-3.5" /> Dismiss</Button>}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}
