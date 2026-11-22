import type { InboxItem, InboxManifestFile } from "@/types";

const providers = new Map([
  ["cults", "Cults3D"],
  ["cults3d", "Cults3D"],
  ["makerworld", "MakerWorld"],
  ["myminifactory", "MyMiniFactory"],
  ["printables", "Printables"],
  ["thingiverse", "Thingiverse"],
  ["thangs", "Thangs"],
  ["crealitycloud", "Creality Cloud"],
  ["makeronline", "MakerOnline"],
]);

export function inboxProviderLabel(item: InboxItem): string | null {
  const host = item.source_hostname;
  const provider =
    item.manifest.schema_version === 2 || item.manifest.schema_version === 3
      ? item.manifest.source.provider
      : host?.replace(/^www\./, "").replace(/\.(com|co)$/, "");
  return (provider && providers.get(provider.toLowerCase())) || host || null;
}

export function inboxFiles(item: InboxItem): InboxManifestFile[] {
  const manifest = item.manifest;
  if (manifest.kind === "direct" || manifest.kind === "browser_file") {
    const name = manifest.filename ?? manifest.title ?? item.display_title;
    return name
      ? [
          {
            id: "direct",
            name,
            size: manifest.size ?? null,
            file_type: name.split(".").at(-1) ?? "",
          },
        ]
      : [];
  }
  if (manifest.kind === "archive") return manifest.entries ?? [];
  if (manifest.kind === "model_files") return manifest.files ?? [];
  if (manifest.kind === "collection") {
    return (manifest.members ?? []).map((member) => ({
      id: member.id,
      name: member.title,
      size: null,
      file_type: "",
    }));
  }
  return [];
}

export function inboxCanImport(item: InboxItem): boolean {
  if (item.state !== "review" || item.review_reason) return false;
  if (item.manifest.kind === "collection" || item.manifest.kind === "archive") return false;
  const files = inboxFiles(item);
  return files.length === 1 || (files.length > 0 && Boolean(item.manifest.selected_ids?.length));
}
