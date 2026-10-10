/** Transferable messages shared by the viewer and its isolated STL worker. */
export type StlParseReply =
  | { state: "parsed"; positions: Float32Array<ArrayBuffer>; normals: Float32Array<ArrayBuffer> }
  | { state: "failed"; reason: string };
