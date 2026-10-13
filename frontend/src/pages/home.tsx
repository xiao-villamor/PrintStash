import { ModelBrowser } from "@/components/model-grid";

// The library starts with navigation-critical reads; auxiliary catalogs follow
// its first usable frame (or an explicit interaction with their controls).
export default function HomePage() {
  return <ModelBrowser initial={undefined} />;
}
