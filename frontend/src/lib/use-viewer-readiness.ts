import { useCallback, useState } from "react";

export interface ViewerReadiness {
  loaded: boolean;
  setLoaded: (loaded: boolean) => void;
}

/** Track readiness by URL so a URL change reads as not loaded during render. */
export function useViewerReadiness(activeUrl: string): ViewerReadiness {
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const setLoaded = useCallback(
    (loaded: boolean) => {
      setLoadedUrl(loaded ? activeUrl : null);
    },
    [activeUrl],
  );

  return { loaded: loadedUrl === activeUrl, setLoaded };
}
