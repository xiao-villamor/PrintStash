const browserBinary = process.env.PRINTSTASH_EXTENSION_BROWSER_BINARY;
const webdriverHost = process.env.PRINTSTASH_EXTENSION_WEBDRIVER_HOST;
const browserName = process.env.PRINTSTASH_EXTENSION_BROWSER_NAME || "chrome";
const extensionDirectory = process.env.PRINTSTASH_EXTENSION_DIST;

const firefoxPopupUrl = process.env.PRINTSTASH_EXTENSION_POPUP_URL;

if (
  !browserBinary ||
  !webdriverHost ||
  !extensionDirectory ||
  (browserName === "firefox" && (!process.env.PRINTSTASH_EXTENSION_XPI || !firefoxPopupUrl))
) {
  throw new Error(
    "Set PRINTSTASH_EXTENSION_BROWSER_BINARY, PRINTSTASH_EXTENSION_WEBDRIVER_HOST, and PRINTSTASH_EXTENSION_DIST to run the loaded-extension smoke test against an installed browser and WebDriver service. Firefox also needs PRINTSTASH_EXTENSION_XPI from pnpm zip:firefox and PRINTSTASH_EXTENSION_POPUP_URL from that profile's installed add-on.",
  );
}

// Chrome 137 removed the `--load-extension` switch, and removed it *silently*:
// the browser still starts, `chrome://extensions` renders an empty list, and every
// assertion about the extension fails with no hint that nothing was installed.
// The extension is now installed at runtime over the browser-level CDP endpoint
// instead (see `installChromeExtension`), which is also better than the old flag:
// it returns the assigned extension id, so the test no longer has to scrape it out
// of the settings page's Polymer shadow DOM.
const chromeOptions = {
  binary: browserBinary,
  args: ["--headless=new"],
};

const firefoxOptions = {
  binary: browserBinary,
  args: ["-headless"],
};

export const config = {
  runner: "local",
  specs: ["./tests/e2e/**/*.e2e.ts"],
  maxInstances: 1,
  hostname: webdriverHost,
  port: Number(process.env.PRINTSTASH_EXTENSION_WEBDRIVER_PORT || 4444),
  path: process.env.PRINTSTASH_EXTENSION_WEBDRIVER_PATH || "/",
  capabilities: [
    browserName === "firefox"
      ? { browserName, "moz:firefoxOptions": firefoxOptions }
      : { browserName, "goog:chromeOptions": chromeOptions },
  ],
  framework: "mocha",
  mochaOpts: { ui: "bdd", timeout: 30_000 },
};
