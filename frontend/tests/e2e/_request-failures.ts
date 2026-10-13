/** Count explicit in-flight fetch cancellations; never exempt an endpoint wholesale. */
export class RequestFailures {
  private readonly cancellations = new Map<string, number>();
  private readonly aborted: { key: string; problem: string }[] = [];

  constructor(private readonly problems: string[]) {}

  cancel(method: string, url: string): void {
    const key = `${method} ${url}`;
    const index = this.aborted.findIndex((failure) => failure.key === key);
    if (index >= 0) {
      const [failure] = this.aborted.splice(index, 1);
      const problemIndex = this.problems.indexOf(failure.problem);
      if (problemIndex >= 0) this.problems.splice(problemIndex, 1);
    } else this.cancellations.set(key, (this.cancellations.get(key) ?? 0) + 1);
  }

  failed(method: string, url: string, error: string): void {
    const key = `${method} ${url}`;
    if (error === "net::ERR_ABORTED") {
      const count = this.cancellations.get(key) ?? 0;
      if (count > 0) {
        this.cancellations.set(key, count - 1);
        return;
      }
    }
    const problem = `request failed: ${url} ${error}`;
    this.problems.push(problem);
    if (error === "net::ERR_ABORTED") this.aborted.push({ key, problem });
  }
}
