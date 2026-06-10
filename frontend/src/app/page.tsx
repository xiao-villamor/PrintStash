import { ModelBrowser, type BrowserInitialData } from "@/components/model-grid";
import { listCollections, listModels, listPrinters, listTags } from "@/lib/api";

export const revalidate = 0;

const PAGE_SIZE = 60;

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q: rawQuery } = await searchParams;
  const query = (rawQuery ?? "").trim();

  // Server-side fetch of the first page + facets so the first paint shows the
  // library instead of skeletons. Mirrors the client's initial query exactly;
  // falls back to client fetching if the API is unreachable during render.
  let initial: BrowserInitialData | undefined;
  try {
    const [models, collections, tags, printers] = await Promise.all([
      listModels({
        limit: PAGE_SIZE,
        offset: 0,
        direct: !query,
        q: query || undefined,
      }),
      listCollections(),
      listTags(),
      listPrinters(),
    ]);
    initial = { models, collections, tags, printers };
  } catch {
    initial = undefined;
  }

  return <ModelBrowser initial={initial} />;
}
