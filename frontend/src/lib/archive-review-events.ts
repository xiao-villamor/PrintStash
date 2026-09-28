const REVIEW_EVENT = "printstash:review-archive";

declare global {
  interface WindowEventMap {
    [REVIEW_EVENT]: CustomEvent<string>;
  }
}

export function requestArchiveReview(jobId: string): void {
  window.dispatchEvent(new CustomEvent<string>(REVIEW_EVENT, { detail: jobId }));
}

export function subscribeArchiveReviewRequests(onRequest: (jobId: string) => void): () => void {
  const listener = (event: CustomEvent<string>) => onRequest(event.detail);
  window.addEventListener(REVIEW_EVENT, listener);
  return () => window.removeEventListener(REVIEW_EVENT, listener);
}
