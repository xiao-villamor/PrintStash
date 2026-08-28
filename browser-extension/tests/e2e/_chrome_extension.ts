/**
 * Installing an unpacked extension into a WebDriver-controlled Chrome.
 *
 * Chrome 137 removed the `--load-extension` command-line switch. It did so
 * without an error path: the browser starts, the extensions page renders empty,
 * and a test that assumed the flag worked fails on whatever it asserted next —
 * which is exactly how this spec came to be red for months with a symptom
 * ("extension not found") that pointed nowhere near the cause.
 *
 * The supported replacement is the `Extensions.loadUnpacked` CDP command, and it
 * lives on the *browser* target rather than the page target ChromeDriver's
 * `send_command_and_get_result` forwards to — calling it there answers "Method
 * not available". So this connects to the browser endpoint directly, using the
 * debugger address ChromeDriver reports back in the session capabilities.
 *
 * The upside over the old flag is real: the command returns the id Chrome
 * assigned, so nothing has to be scraped out of `chrome://extensions`. That page
 * is a Polymer app whose nested shadow roots are internal implementation, and
 * walking them was the other half of what made this test brittle.
 */

import WebSocket from "ws";

interface DebuggableBrowser {
  capabilities: { "goog:chromeOptions"?: { debuggerAddress?: string } };
}

interface LoadUnpackedReply {
  id: number;
  error?: { message?: string };
  result?: { id?: string };
}

export async function installChromeExtension(
  browser: DebuggableBrowser,
  directory: string,
): Promise<string> {
  const address = browser.capabilities["goog:chromeOptions"]?.debuggerAddress;
  if (!address) {
    throw new Error(
      "ChromeDriver reported no debuggerAddress, so the browser CDP endpoint cannot be reached to install the extension.",
    );
  }

  const response = await fetch(`http://${address}/json/version`);
  const { webSocketDebuggerUrl } = (await response.json()) as {
    webSocketDebuggerUrl: string;
  };

  const socket = new WebSocket(webSocketDebuggerUrl);
  try {
    await new Promise<void>((resolve, reject) => {
      socket.once("open", () => resolve());
      socket.once("error", reject);
    });
    const reply = await new Promise<LoadUnpackedReply>((resolve, reject) => {
      socket.once("message", (data: Buffer) => {
        resolve(JSON.parse(data.toString()) as LoadUnpackedReply);
      });
      socket.once("error", reject);
      socket.send(
        JSON.stringify({
          id: 1,
          method: "Extensions.loadUnpacked",
          params: { path: directory },
        }),
      );
    });
    if (reply.error || !reply.result?.id) {
      throw new Error(
        `Extensions.loadUnpacked failed: ${reply.error?.message ?? "no extension id returned"}`,
      );
    }
    return reply.result.id;
  } finally {
    socket.close();
  }
}
