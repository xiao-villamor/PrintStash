/**
 * Opening Upload from the shell. A mounted Library opens its dialog directly,
 * so the request neither rewrites the URL nor re-renders the browsing surface;
 * without a Library to take it, the caller navigates with `?upload=1`.
 */
type UploadListener = () => void;

const listeners = new Set<UploadListener>();

export function subscribeUploadRequests(listener: UploadListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Whether a mounted Library accepted the request. */
export function requestUpload(): boolean {
  if (listeners.size === 0) return false;
  for (const listener of listeners) listener();
  return true;
}
