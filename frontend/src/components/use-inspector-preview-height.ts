import { useLayoutEffect, useState, type RefObject } from "react";

export const MIN_HEIGHT = 180;
const HEIGHT_KEY = "printstash.inspector-preview-height";

function savedHeight() {
  try {
    const value = Number(localStorage.getItem(HEIGHT_KEY));
    return value > 0 && Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

export function useInspectorPreviewHeight(scroll: RefObject<HTMLDivElement | null>) {
  const [preferred, setPreferred] = useState(savedHeight);
  const [available, setAvailable] = useState(0);
  useLayoutEffect(() => {
    const element = scroll.current;
    if (!element) return;
    const measure = () => {
      const headerHeight = element.firstElementChild?.getBoundingClientRect().height ?? 0;
      setAvailable(element.clientHeight - headerHeight);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [scroll]);

  const max = Math.max(MIN_HEIGHT, Math.floor(available * 0.85));
  const clamp = (value: number) => Math.max(MIN_HEIGHT, Math.min(max, value));
  return {
    height: clamp(preferred ?? Math.round(available * 0.55)),
    max,
    resize(value: number | null) {
      const next = value === null ? null : clamp(value);
      setPreferred(next);
      try {
        if (next === null) localStorage.removeItem(HEIGHT_KEY);
        else localStorage.setItem(HEIGHT_KEY, String(next));
      } catch {
        // Keep the preference for this session when storage is unavailable.
      }
    },
  };
}
