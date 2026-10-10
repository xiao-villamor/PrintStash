import { useEffect, useState } from "react";
import { BufferGeometry, BufferAttribute } from "three";
import { parseStl, type StlWorkerFactory } from "./stl-parser";

type ParsedStl =
  | { state: "loading" }
  | { state: "ready"; url: string; geometry: BufferGeometry }
  | { state: "failed"; url: string; error: Error };

export function useParsedStl(url: string | null, createWorker?: StlWorkerFactory): ParsedStl {
  const [result, setResult] = useState<ParsedStl>({ state: "loading" });
  useEffect(() => {
    if (url === null) return;
    const controller = new AbortController();
    let geometry: BufferGeometry | null = null;
    void parseStl(url, controller.signal, createWorker)
      .then((parsed) => {
        if (controller.signal.aborted) return;
        geometry = new BufferGeometry();
        geometry.setAttribute("position", new BufferAttribute(parsed.positions, 3));
        geometry.setAttribute("normal", new BufferAttribute(parsed.normals, 3));
        setResult({ state: "ready", url, geometry });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setResult({ state: "failed", url, error });
      });
    return () => {
      controller.abort();
      geometry?.dispose();
    };
  }, [url, createWorker]);
  return result.state !== "loading" && result.url === url ? result : { state: "loading" };
}
