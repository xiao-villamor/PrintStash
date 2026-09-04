"use client";

import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";

/** Renders a markdown <img> by fetching the authenticated blob (same path as
 *  thumbnails — the bearer token can't ride along on a raw <img src>). */
function AuthImage({ src, alt }: { src?: string; alt?: string }) {
  // External (http) images load directly; our own /api/... images need auth.
  const isLocal = !!src && src.startsWith("/api/");
  const blobUrl = useAuthenticatedAssetUrl(isLocal ? src : null);
  const resolved = isLocal ? blobUrl : src;
  if (!resolved) return null;
  return <img src={resolved} alt={alt ?? ""} className="max-w-full rounded" />;
}

const components = {
  img: AuthImage,
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow">
      {children}
    </a>
  ),
};

/** Sanitized markdown renderer wrapped in Tailwind `prose`. The single place
 *  untrusted markdown (pasted, scraped, uploaded) is turned into DOM. */
export function MarkdownView({ source, className }: { source: string; className?: string }) {
  return (
    <div className={`prose prose-sm dark:prose-invert max-w-none ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={components}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
