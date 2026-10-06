/**
 * Install through WebDriver BiDi so ChromeDriver owns the browser pipe.
 * Chromium restricts extension installation to clients launched with both
 * remote-debugging-pipe and enable-unsafe-extension-debugging. A separate CDP
 * WebSocket cannot gain that authority, even on the browser target.
 */
interface ExtensionInstaller {
  webExtensionInstall(parameters: {
    extensionData: { type: "path"; path: string };
  }): Promise<{ extension: string }>;
}

export async function installChromeExtension(
  browser: ExtensionInstaller,
  directory: string,
): Promise<string> {
  const { extension } = await browser.webExtensionInstall({
    extensionData: { type: "path", path: directory },
  });
  if (!/^[a-p]{32}$/.test(extension)) {
    throw new Error("WebDriver returned no valid Chrome extension id");
  }
  return extension;
}
