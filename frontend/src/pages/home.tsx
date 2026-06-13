import { ModelBrowser } from "@/components/model-grid";

// Client SPA: no SSR prefetch. ModelBrowser fetches the first page + facets on
// mount (with the localStorage token), which is what the old server fallback
// path did anyway.
export default function HomePage() {
  return <ModelBrowser initial={undefined} />;
}
