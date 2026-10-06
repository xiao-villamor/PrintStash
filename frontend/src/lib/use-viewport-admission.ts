"use client";

import { useEffect, useState } from "react";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";

const observers = new Map<Element, () => void>();
let observer: IntersectionObserver | null = null;

function admitNearViewport(node: Element, admit: () => void): () => void {
  if (globalThis.IntersectionObserver === undefined) {
    admit();
    return () => {};
  }
  observer ??= new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const admit = observers.get(entry.target);
        observers.delete(entry.target);
        observer?.unobserve(entry.target);
        admit?.();
      }
      if (observers.size === 0) {
        observer?.disconnect();
        observer = null;
      }
    },
    { rootMargin: "200px" },
  );
  observers.set(node, admit);
  observer.observe(node);
  return () => {
    observers.delete(node);
    observer?.unobserve(node);
    if (observers.size === 0) {
      observer?.disconnect();
      observer = null;
    }
  };
}

/** Once admitted, a mounted image keeps its lease even when scrolled out of view. */
export function useViewportAdmission() {
  const [node, ref] = useState<HTMLElement | null>(null);
  const [admitted, setAdmitted] = useState(false);
  useEffect(() => {
    if (!node || admitted) return;
    return admitNearViewport(node, () => setAdmitted(true));
  }, [node, admitted]);
  return { ref, admitted };
}

/** Attach ref to a persistent thumbnail frame, preserving its existing semantics. */
export function useViewportAssetUrl(path: string | null | undefined) {
  const { ref, admitted } = useViewportAdmission();
  const url = useAuthenticatedAssetUrl(path, admitted);
  return { ref, url };
}
