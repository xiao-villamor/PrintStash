/** Let component tests drive upload progress while routes still use the fetch recorder. */
interface UploadProgressTarget {
  onprogress: ((event: ProgressEvent) => void) | null;
}

export class FetchBackedXhr {
  static requests: FetchBackedXhr[] = [];

  readonly upload: UploadProgressTarget = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 0;
  responseText = "";
  method = "";
  url = "";
  body: FormData | null = null;
  headers: Record<string, string> = {};
  private controller = new AbortController();

  constructor() {
    FetchBackedXhr.requests.push(this);
  }

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value;
  }

  send(body: FormData): void {
    this.body = body;
    void fetch(this.url, {
      method: this.method,
      headers: this.headers,
      body,
      signal: this.controller.signal,
    })
      .then(async (response) => {
        if (this.controller.signal.aborted) return;
        this.status = response.status;
        this.responseText = await response.text();
        this.onload?.();
      })
      .catch(() => {
        if (!this.controller.signal.aborted) this.onerror?.();
      });
  }

  abort(): void {
    this.controller.abort();
    this.onabort?.();
  }

  emitProgress(loaded: number, total: number): void {
    this.upload.onprogress?.(
      new ProgressEvent("progress", { loaded, total, lengthComputable: true }),
    );
  }
}
