import { defineConfig } from "wxt";
import { resolve } from "node:path";

export default defineConfig({
  hooks: {
    "build:publicAssets": (_wxt, files) => {
      for (const fileName of [
        "icon.svg",
        "icon-16.png",
        "icon-32.png",
        "icon-48.png",
        "icon-128.png",
      ]) {
        files.push({ absoluteSrc: resolve(fileName), relativeDest: fileName });
      }
    },
  },
  manifest: {
    name: "PrintStash Model Importer",
    description:
      "Send models from MakerWorld, Printables, Thingiverse, or direct file links to PrintStash.",
    permissions: ["activeTab", "scripting", "storage"],
    host_permissions: ["http://localhost/*", "http://127.0.0.1/*", "http://[::1]/*"],
    optional_host_permissions: ["http://*/*", "https://*/*"],
    browser_specific_settings: {
      gecko: {
        id: "printstash-model-importer@printstash.local",
        data_collection_permissions: {
          required: ["authenticationInfo", "browsingActivity", "websiteContent"],
          optional: [],
        },
      },
    },
    action: {
      default_title: "Import model to PrintStash",
      default_icon: {
        16: "icon-16.png",
        32: "icon-32.png",
      },
    },
    icons: {
      16: "icon-16.png",
      32: "icon-32.png",
      48: "icon-48.png",
      128: "icon-128.png",
    },
  },
});
