/** Page identities, never download hosts or provider credentials. */
export const SOURCE_PROVIDERS = [
  {
    code: "makerworld",
    label: "MakerWorld",
    hosts: ["makerworld.com", "www.makerworld.com"],
    path: /^\/(?:[a-z]{2}(?:-[a-z]{2,})?\/)?models\/(\d+)(?:-[^/]+)?\/?$/i,
  },
  {
    code: "printables",
    label: "Printables",
    hosts: ["printables.com", "www.printables.com"],
    path: /^\/model\/(\d+)(?:-[^/]+)?(?:\/files)?\/?$/i,
  },
  {
    code: "thingiverse",
    label: "Thingiverse",
    hosts: ["thingiverse.com", "www.thingiverse.com"],
    path: /^\/(?:thing:|things\/)(\d+)(?:\/files)?\/?$/i,
  },
  {
    code: "myminifactory",
    label: "MyMiniFactory",
    hosts: ["myminifactory.com", "www.myminifactory.com"],
    path: /^\/object\/([^/]+)\/?$/i,
  },
  {
    code: "cults",
    label: "Cults",
    hosts: ["cults3d.com", "www.cults3d.com"],
    path: /^\/(?:[a-z]{2}\/)?3d-model\/[^/]+\/([^/]+)\/?$/i,
  },
  {
    code: "thangs",
    label: "Thangs",
    hosts: ["thangs.com", "www.thangs.com"],
    path: /^\/designer\/[^/]+\/3d-model\/(?:[^/]+-)?(\d+)\/?$/i,
  },
  {
    code: "crealitycloud",
    label: "Creality Cloud",
    hosts: ["crealitycloud.com", "www.crealitycloud.com"],
    path: /^\/(?:[a-z]{2}\/)?model-detail\/([^/]+)\/?$/i,
  },
  {
    code: "makeronline",
    label: "MakerOnline",
    hosts: ["makeronline.com", "www.makeronline.com"],
    path: /^\/(?:[a-z]{2}\/)?model\/[^/]+\/(\d+)\.html\/?$/i,
  },
] as const;
export type SourceProviderCode = (typeof SOURCE_PROVIDERS)[number]["code"];
export type SourceProviderLabel = (typeof SOURCE_PROVIDERS)[number]["label"];
export const MARKETPLACE_ORIGINS = SOURCE_PROVIDERS.flatMap((provider) =>
  provider.hosts.map((host) => `https://${host}/*`),
);

export function sourcePublication(
  value: string,
): { code: SourceProviderCode; label: SourceProviderLabel; itemId: string } | null {
  try {
    // Inspect the raw spelling before URL normalizes encoded traversal away.
    if (/%(?:2f|5c|2e|00)|\\/i.test(value)) return null;
    const url = new URL(value);
    if (url.protocol !== "https:" || url.port || url.username || url.password) return null;
    const provider = SOURCE_PROVIDERS.find((provider) =>
      provider.hosts.some((host) => host === url.hostname),
    );
    const match = provider?.path.exec(url.pathname);
    const itemId = match?.[1];
    if (!provider || !itemId) return null;
    return { code: provider.code, label: provider.label, itemId };
  } catch {
    return null;
  }
}
