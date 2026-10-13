import { STLLoader } from "three/addons/loaders/STLLoader.js";
import type { StlParseReply } from "@/lib/stl-parser-types";

// This module is bundled as a dedicated Worker; it never runs on Window.
self.onmessage = (event: MessageEvent<ArrayBuffer>) => {
  const geometry = new STLLoader().parse(event.data);
  try {
    const positions = new Float32Array(geometry.getAttribute("position").array);
    const normals = new Float32Array(geometry.getAttribute("normal").array);
    if (
      positions.length === 0 ||
      positions.length % 9 !== 0 ||
      normals.length !== positions.length ||
      !positions.every(Number.isFinite) ||
      !normals.every(Number.isFinite)
    ) {
      throw new Error("stl_geometry_invalid");
    }
    const reply: StlParseReply = { state: "parsed", positions, normals };
    self.postMessage(reply, { transfer: [positions.buffer, normals.buffer] });
  } finally {
    geometry.dispose();
  }
};
