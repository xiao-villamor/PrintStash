/** Browser transport stand-in: routes XHR bytes through the test's HTTP boundary. */
export class UploadRequest {
  static instances: UploadRequest[] = [];
  method = "";
  url = "";
  headers = new Headers();
  timeout = 0;
  withCredentials = false;
  status = 0;
  responseText = "";
  responseHeaders = new Headers();
  controller = new AbortController();
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    UploadRequest.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers.set(name, value);
  }
  getResponseHeader(name: string) {
    return this.responseHeaders.get(name);
  }
  abort() {
    this.controller.abort();
    this.onabort?.();
  }
  send(body: Blob) {
    void fetch(this.url, {
      method: this.method,
      body,
      headers: Object.fromEntries(this.headers),
      signal: this.controller.signal,
    }).then(
      async (response) => {
        if (this.controller.signal.aborted) return;
        this.upload.onprogress?.(
          new ProgressEvent("progress", {
            loaded: body.size,
            total: body.size,
            lengthComputable: true,
          }),
        );
        this.status = response.status;
        this.responseHeaders = response.headers;
        this.responseText = await response.text();
        this.onload?.();
      },
      () => {
        if (!this.controller.signal.aborted) this.onerror?.();
      },
    );
  }
}
