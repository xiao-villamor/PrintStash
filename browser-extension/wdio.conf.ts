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

const chromeOptions = {
  binary: browserBinary,
  args: [
    "--headless=new",
    `--disable-extensions-except=${extensionDirectory}`,
    `--load-extension=${extensionDirectory}`,
  ],
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
