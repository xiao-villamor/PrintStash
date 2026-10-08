"use client";

import { useEffect, useState } from "react";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";

type AdmissionGroup = {
  callbacks: Map<Element, () => void>;
  observer: IntersectionObserver;
};
const groups = new Map<string, AdmissionGroup>();

function admitNearViewport(node: Element, admit: () => void, rootMargin: string): () => void {
  if (globalThis.IntersectionObserver === undefined) {
    admit();
    return () => {};
  }
  let group = groups.get(rootMargin);
  if (!group) {
    const callbacks = new Map<Element, () => void>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const accept = callbacks.get(entry.target);
          callbacks.delete(entry.target);
          observer.unobserve(entry.target);
          accept?.();
        }
        if (callbacks.size === 0) {
          observer.disconnect();
          groups.delete(rootMargin);
        }
      },
      { rootMargin },
    );
    group = { callbacks, observer };
    groups.set(rootMargin, group);
  }
  const { callbacks, observer } = group;
  callbacks.set(node, admit);
  observer.observe(node);
  return () => {
    callbacks.delete(node);
    observer.unobserve(node);
    if (callbacks.size === 0) {
      observer.disconnect();
      if (groups.get(rootMargin) === group) groups.delete(rootMargin);
    }
  };
}

/** Once admitted, a mounted image keeps its lease even when scrolled out of view. */
export function useViewportAdmission() {
  const { complete } = useLibraryStartup();
  const margin = complete ? "200px" : "0px";
  const [node, ref] = useState<HTMLElement | null>(null);
  const [admitted, setAdmitted] = useState(false);
  useEffect(() => {
    if (!node || admitted) return;
    return admitNearViewport(node, () => setAdmitted(true), margin);
  }, [node, admitted, margin]);
  return { ref, admitted };
}

/** Attach ref to a persistent thumbnail frame, preserving its existing semantics. */
export function useViewportAssetUrl(path: string | null | undefined) {
  const { ref, admitted } = useViewportAdmission();
  const url = useAuthenticatedAssetUrl(path, admitted);
  return { ref, url };
}
