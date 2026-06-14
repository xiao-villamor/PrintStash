// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

// PrintStash documentation site (Starlight).
// `site` is the GitHub Pages project URL; adjust if you deploy elsewhere.
export default defineConfig({
  site: "https://xiao-villamor.github.io",
  base: "/PrintStash",
  integrations: [
    starlight({
      title: "PrintStash",
      description:
        "Self-hosted asset manager for people who 3D print more things than they can remember.",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/xiao-villamor/PrintStash",
        },
      ],
      editLink: {
        baseUrl:
          "https://github.com/xiao-villamor/PrintStash/edit/main/docs/wiki/",
      },
      sidebar: [
        {
          label: "Getting Started",
          items: [
            { label: "Overview", slug: "getting-started/overview" },
            { label: "Installation", slug: "getting-started/installation" },
            { label: "Configuration", slug: "getting-started/configuration" },
          ],
        },
        {
          label: "Guides",
          items: [
            { label: "User guide", slug: "guides/user-guide" },
            { label: "Printers & providers", slug: "guides/printers" },
          ],
        },
        {
          label: "Reference",
          items: [
            { label: "API", slug: "reference/api" },
            { label: "Architecture", slug: "reference/architecture" },
            {
              label: "Known limitations",
              slug: "reference/known-limitations",
            },
          ],
        },
        {
          label: "Contributing",
          items: [{ label: "Development", slug: "contributing/development" }],
        },
      ],
    }),
  ],
});
